"""
Tests for PriceCache and ResilientPriceEngine.
All tests run offline — no network calls required.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from engine.price_cache import _PriceCache, price_cache
from engine.resilient_price_engine import (
    ResilientPriceEngine,
    _CircuitBreaker,
    _safe_float_env,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_price_cache():
    """Reset the singleton between every test so state doesn't leak."""
    _PriceCache._instance = None
    yield
    _PriceCache._instance = None


# ── PriceCache Tests ─────────────────────────────────────────────────────────

class TestPriceCacheSingleton:
    def test_same_object(self):
        a = _PriceCache()
        b = _PriceCache()
        assert a is b


class TestPriceCacheGet:
    def test_cache_hit(self):
        pc = _PriceCache()
        pc.set_ttl(10.0)
        calls = []

        def fetch():
            calls.append(1)
            return 2500.0

        assert pc.get(fetch) == 2500.0
        assert pc.get(fetch) == 2500.0  # second call should be cached
        assert len(calls) == 1  # fetch_fn only called once

    def test_cache_miss_after_expiry(self, monkeypatch):
        pc = _PriceCache()
        pc.set_ttl(0.0)  # TTL=0 → always expired

        counter = {"n": 0}

        def fetch():
            counter["n"] += 1
            return 3000.0 + counter["n"]

        first = pc.get(fetch)
        second = pc.get(fetch)
        assert counter["n"] == 2
        assert first != second

    def test_invalidate_forces_refetch(self):
        pc = _PriceCache()
        pc.set_ttl(60.0)
        calls = []

        def fetch():
            calls.append(1)
            return 1800.0

        pc.get(fetch)
        assert len(calls) == 1

        pc.invalidate()
        pc.get(fetch)
        assert len(calls) == 2

    def test_fallback_returns_stale(self):
        pc = _PriceCache()
        pc.set_ttl(60.0)

        # Seed the cache with a good price
        pc.get(lambda: 2700.0)
        assert pc.peek() == 2700.0

        # Force expiry by back-dating the timestamp (keep stale _price intact)
        pc._timestamp = 0.0
        result = pc.get(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert result == 2700.0

    def test_fallback_default_when_no_stale(self, monkeypatch):
        monkeypatch.setenv("FALLBACK_ETH_PRICE", "1234.0")
        _PriceCache._instance = None  # re-create to pick up env
        pc = _PriceCache()

        result = pc.get(lambda: (_ for _ in ()).throw(RuntimeError("no data")))
        assert result == 1234.0

    def test_fallback_default_uses_2000(self):
        pc = _PriceCache()
        result = pc.get(lambda: (_ for _ in ()).throw(RuntimeError("no data")))
        assert result == 2000.0


class TestPriceCacheStats:
    def test_hits_and_misses(self):
        pc = _PriceCache()
        pc.set_ttl(60.0)

        pc.get(lambda: 100.0)   # miss
        pc.get(lambda: 100.0)   # hit
        pc.get(lambda: 100.0)   # hit

        s = pc.stats()
        assert s["misses"] == 1
        assert s["hits"] == 2
        assert s["hit_ratio"] == round(2 / 3, 4)


class TestPriceCachePeek:
    def test_peek_empty(self):
        pc = _PriceCache()
        assert pc.peek() is None

    def test_peek_populated(self):
        pc = _PriceCache()
        pc.get(lambda: 4200.0)
        assert pc.peek() == 4200.0


class TestPriceCacheSetTTL:
    def test_set_ttl_changes_behaviour(self):
        pc = _PriceCache()
        pc.set_ttl(0.0)  # always expired
        calls = []

        def fetch():
            calls.append(1)
            return 5000.0

        pc.get(fetch)
        pc.get(fetch)
        assert len(calls) == 2  # both are misses (TTL=0)

        # Lengthen TTL — the last fetch already cached the price with
        # a fresh timestamp, so both subsequent calls are cache hits.
        pc.set_ttl(60.0)
        pc.get(fetch)
        pc.get(fetch)
        assert len(calls) == 2  # no new fetches — both are hits


# ── ResilientPriceEngine Tests ───────────────────────────────────────────────

class TestResilientPriceEngineGetPrices:
    @pytest.mark.asyncio
    async def test_static_fallback_when_all_fail(self):
        engine = ResilientPriceEngine(supervisor=None)
        # No supervisor → Rust skipped; DEX imports will fail → Python skipped;
        # CoinGecko uses real HTTP which we don't patch → fails → fallback
        # Force circuit breakers open for gecko/python so they skip instantly
        engine._cb_python.record_failure()
        engine._cb_gecko.record_failure()

        prices = await engine.get_prices()
        assert "uniswap_v3" in prices
        assert "sushiswap" in prices
        assert prices["uniswap_v3"] == prices["sushiswap"]
        assert prices["uniswap_v3"] > 0
        await engine.close()


class TestResilientPriceEngineStats:
    @pytest.mark.asyncio
    async def test_fallback_hits_increment(self):
        engine = ResilientPriceEngine(supervisor=None)
        engine._cb_python.record_failure()
        engine._cb_gecko.record_failure()

        await engine.get_prices()
        await engine.get_prices()

        s = engine.stats()
        assert s["fallback_hits"] == 2
        assert s["total_calls"] == 2
        await engine.close()

    @pytest.mark.asyncio
    async def test_stats_percentages(self):
        engine = ResilientPriceEngine(supervisor=None)
        engine._cb_python.record_failure()
        engine._cb_gecko.record_failure()

        await engine.get_prices()

        s = engine.stats()
        assert s["total_calls"] == 1
        assert s["fallback_pct"] == 100.0
        assert s["rust_pct"] == 0.0
        await engine.close()


# ── CircuitBreaker Tests ─────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_allows_on_first_call(self):
        cb = _CircuitBreaker("test")
        assert cb.allow() is True

    def test_blocks_after_failure(self):
        cb = _CircuitBreaker("test")
        cb.record_failure()
        assert cb.allow() is False

    def test_allows_after_timeout(self, monkeypatch):
        cb = _CircuitBreaker("test")
        cb.record_failure()
        assert cb.allow() is False

        # Simulate time passing beyond CIRCUIT_OPEN_SECS
        import engine.resilient_price_engine as mod
        original = mod.CIRCUIT_OPEN_SECS
        monkeypatch.setattr(mod, "CIRCUIT_OPEN_SECS", 0.0)
        assert cb.allow() is True
        monkeypatch.setattr(mod, "CIRCUIT_OPEN_SECS", original)

    def test_record_success_resets(self):
        cb = _CircuitBreaker("test")
        cb.record_failure()
        assert cb.allow() is False
        cb.record_success()
        assert cb.allow() is True


# ── _safe_float_env Tests ────────────────────────────────────────────────────

class TestSafeFloatEnv:
    def test_missing_env_returns_default(self, monkeypatch):
        monkeypatch.delenv("__TEST_MISSING_KEY__", raising=False)
        assert _safe_float_env("__TEST_MISSING_KEY__", 42.0) == 42.0

    def test_invalid_env_returns_default(self, monkeypatch):
        monkeypatch.setenv("__TEST_BAD_FLOAT__", "not_a_number")
        assert _safe_float_env("__TEST_BAD_FLOAT__", 99.0) == 99.0

    def test_valid_env_returns_parsed(self, monkeypatch):
        monkeypatch.setenv("__TEST_GOOD_FLOAT__", "3.14")
        assert _safe_float_env("__TEST_GOOD_FLOAT__", 0.0) == pytest.approx(3.14)
