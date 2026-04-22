"""
resilient_price_engine.py — Cross-language fault-tolerant price aggregator.

Priority chain (tried in order, falls through on any failure):

  1. Rust dex-oracle  (:9001/prices)   ~0.5 ms local HTTP
     ↓ fail/crash/timeout
  2. Python async     (asyncio.gather) ~300-600 ms concurrent eth_call
     ↓ fail (RPC unavailable)
  3. Python CoinGecko (shared cache)   ~200-500 ms HTTPS, TTL-cached
     ↓ fail (no internet)
  4. Static fallback  ($2000 config)   ~0 µs, always succeeds

Each layer is tried independently. A Rust crash does NOT crash Python.
A Python exception does NOT stop Rust from continuing to serve other callers.

Key design decisions:
  - asyncio.gather() for concurrent Python fallback calls
  - Shared PriceCache singleton — one CoinGecko call per TTL window regardless
    of how many callers hit this simultaneously
  - Circuit breaker per source — broken source is skipped for CIRCUIT_OPEN_SECS
    before retrying, preventing latency pileup on a dead RPC
  - All paths return the same base dict shape: {"uniswap_v3": float, "sushiswap": float}
    and include {"kalman_filtered": float} when the optional Kalman filter is available
"""

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger("aureon.price_engine")

# ── Configuration ─────────────────────────────────────────────────────────────

def _safe_float_env(key: str, default: float) -> float:
    """Parse a float from an environment variable, falling back to default on error."""
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        logger.warning("Invalid %s=%r, using default %.1f", key, val, default)
        return default

FALLBACK_PRICE     = _safe_float_env("FALLBACK_ETH_PRICE", 2000.0)
RUST_TIMEOUT       = _safe_float_env("RUST_SIDECAR_TIMEOUT", 0.5)
PYTHON_RPC_TIMEOUT = _safe_float_env("PYTHON_RPC_TIMEOUT", 8.0)
CIRCUIT_OPEN_SECS  = _safe_float_env("CIRCUIT_OPEN_SECS", 30.0)


class _CircuitBreaker:
    """
    Tracks failures for a single source.
    Opens (disables) the source for CIRCUIT_OPEN_SECS after a failure,
    then half-opens (tries once) to check recovery.
    """
    def __init__(self, name: str):
        self.name        = name
        self._failures   = 0
        self._opened_at  = 0.0
        self._open       = False

    def record_success(self):
        self._failures = 0
        self._open     = False

    def record_failure(self):
        self._failures += 1
        self._open      = True
        self._opened_at = time.monotonic()
        logger.warning("Circuit OPEN: %s (failure #%d)", self.name, self._failures)

    def allow(self) -> bool:
        if not self._open:
            return True
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= CIRCUIT_OPEN_SECS:
            logger.info("Circuit HALF-OPEN: retrying %s", self.name)
            return True  # half-open probe
        return False


class ResilientPriceEngine:
    """
    Four-layer price source with automatic cross-language failover.

    Designed to run inside the asyncio event loop — all methods are async
    and safe to call concurrently from multiple tasks.
    """

    def __init__(self, supervisor=None):
        """
        Args:
            supervisor: Supervisor instance (optional). If None, skips Rust path.
        """
        self._supervisor = supervisor
        self._http       = httpx.AsyncClient(timeout=RUST_TIMEOUT)

        # Per-source circuit breakers
        self._cb_rust    = _CircuitBreaker("rust-dex-oracle")
        self._cb_python  = _CircuitBreaker("python-async-rpc")
        self._cb_gecko   = _CircuitBreaker("coingecko")

        # Lazy imports — only loaded if the Python RPC path is triggered
        self._uni   = None
        self._sushi = None

        # Shared price cache (singleton from engine/price_cache.py)
        self._cache = None

        # Kalman filter for price smoothing (soft-fail)
        self._kalman = None
        self._filtered_price: float = 0.0

        self._stats = {
            "rust_hits":   0,
            "python_hits": 0,
            "gecko_hits":  0,
            "fallback_hits": 0,
            "total_calls": 0,
        }

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_prices(self) -> dict:
        """
        Return ETH price map from the fastest available source.
        Always returns a dict — never raises.
        """
        self._stats["total_calls"] += 1

        prices = await self._try_rust()
        if prices:
            self._stats["rust_hits"] += 1
            return await self._attach_kalman(prices)

        prices = await self._try_python_rpc()
        if prices:
            self._stats["python_hits"] += 1
            return await self._attach_kalman(prices)

        prices = await self._try_coingecko()
        if prices:
            self._stats["gecko_hits"] += 1
            return await self._attach_kalman(prices)

        # Layer 4: static fallback — always succeeds
        self._stats["fallback_hits"] += 1
        logger.error("ALL price sources failed — using static fallback $%.2f", FALLBACK_PRICE)
        return await self._attach_kalman(
            {"uniswap_v3": FALLBACK_PRICE, "sushiswap": FALLBACK_PRICE}
        )

    async def _attach_kalman(self, prices: dict) -> dict:
        """Add a 'kalman_filtered' key with the IMM-UKF smoothed price."""
        try:
            if self._kalman is None:
                from nexus_arb.kalman_filter import KalmanFilter
                self._kalman = KalmanFilter()

            ref = float(next(iter(prices.values())))
            state = await self._kalman.update(ref)
            filtered_price = float(state.price_est)
            self._filtered_price = filtered_price
            prices["kalman_filtered"] = round(filtered_price, 4)
        except ImportError as exc:
            logger.debug("Kalman filter unavailable: %s", exc)
        except (StopIteration, TypeError, ValueError, AttributeError) as exc:
            logger.debug("Kalman filter update skipped: %s", exc)
        return prices

    def stats(self) -> dict:
        s = dict(self._stats)
        total = s["total_calls"] or 1
        s["rust_pct"]     = round(s["rust_hits"]     / total * 100, 1)
        s["python_pct"]   = round(s["python_hits"]   / total * 100, 1)
        s["gecko_pct"]    = round(s["gecko_hits"]     / total * 100, 1)
        s["fallback_pct"] = round(s["fallback_hits"]  / total * 100, 1)
        return s

    async def close(self):
        await self._http.aclose()

    # ── Layer 1: Rust dex-oracle ──────────────────────────────────────────────

    async def _try_rust(self) -> Optional[dict]:
        if not self._supervisor:
            return None
        if not self._supervisor.dex_ok():
            return None
        if not self._cb_rust.allow():
            return None

        try:
            data = await self._supervisor.fetch_prices()
            if data and isinstance(data, dict):
                prices = {
                    k: float(v) for k, v in data.items()
                    if isinstance(v, (int, float)) and k not in ("cache_hit", "arbitrage")
                }
                prices.pop("source", None)
                if prices:
                    self._cb_rust.record_success()
                    return prices
        except Exception as exc:
            logger.debug("Rust layer failed: %s", exc)
            self._cb_rust.record_failure()
        return None

    # ── Layer 2: Python async RPC ─────────────────────────────────────────────

    async def _try_python_rpc(self) -> Optional[dict]:
        if not self._cb_python.allow():
            return None

        try:
            self._ensure_dex_clients()
            if not self._uni and not self._sushi:
                return None

            tasks = []
            if self._uni:
                tasks.append(self._uni_price_async())
            if self._sushi:
                tasks.append(self._sushi_price_async())

            results = await asyncio.gather(*tasks, return_exceptions=True)
            prices  = {}

            if self._uni and len(results) > 0:
                r = results[0]
                if isinstance(r, (int, float)) and not isinstance(r, bool) and r > 0:
                    prices["uniswap_v3"] = float(r)
            if self._sushi:
                idx = 1 if self._uni else 0
                if len(results) > idx:
                    r = results[idx]
                    if isinstance(r, (int, float)) and not isinstance(r, bool) and r > 0:
                        prices["sushiswap"] = float(r)

            if prices:
                self._cb_python.record_success()
                return prices

        except Exception as exc:
            logger.debug("Python RPC layer failed: %s", exc)
            self._cb_python.record_failure()
        return None

    def _ensure_dex_clients(self) -> None:
        """Lazy-import DEX clients only when the Python fallback is needed."""
        if self._uni is None and self._sushi is None:
            rpc = os.getenv("RPC_URL") or os.getenv("ETH_RPC", "")
            if not rpc:
                logger.debug("No RPC_URL — Python DEX path unavailable")
                return

            try:
                from engine.dex.uniswap_v3 import UniswapV3
                self._uni = UniswapV3(rpc)
            except Exception as exc:
                logger.debug("UniswapV3 client unavailable: %s", exc)

            try:
                from engine.dex.sushiswap import SushiSwap
                self._sushi = SushiSwap(rpc)
            except Exception as exc:
                logger.debug("SushiSwap client unavailable: %s", exc)

    async def _uni_price_async(self) -> float:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._uni.get_best_eth_price)

    async def _sushi_price_async(self) -> float:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sushi.get_eth_price_usdc)

    # ── Layer 3: CoinGecko (shared TTL cache) ─────────────────────────────────

    async def _try_coingecko(self) -> Optional[dict]:
        if not self._cb_gecko.allow():
            return None

        try:
            self._ensure_cache()
            if self._cache:
                def _fetch():
                    import requests
                    resp = requests.get(
                        "https://api.coingecko.com/api/v3/simple/price"
                        "?ids=ethereum&vs_currencies=usd",
                        timeout=5
                    )
                    resp.raise_for_status()
                    return resp.json()["ethereum"]["usd"]

                loop  = asyncio.get_running_loop()
                price = await loop.run_in_executor(None, lambda: self._cache.get(_fetch))
            else:
                # No cache available — raw async call
                async with httpx.AsyncClient(timeout=5) as client:
                    resp  = await client.get(
                        "https://api.coingecko.com/api/v3/simple/price"
                        "?ids=ethereum&vs_currencies=usd"
                    )
                    price = resp.json()["ethereum"]["usd"]

            if price and price > 0:
                self._cb_gecko.record_success()
                base = float(price)
                # Return both DEX names with same base price (spread = 0, realistic for fallback)
                return {"uniswap_v3": base, "sushiswap": base}

        except Exception as exc:
            logger.debug("CoinGecko layer failed: %s", exc)
            self._cb_gecko.record_failure()
        return None

    def _ensure_cache(self):
        if self._cache is None:
            try:
                from engine.price_cache import price_cache
                self._cache = price_cache
            except Exception as exc:
                logger.debug("Price cache init failed: %s", exc)
