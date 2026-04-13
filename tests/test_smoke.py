"""
Smoke tests — verify core components initialize and the API responds.
All tests run offline; network calls are either mocked or skipped.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Config ────────────────────────────────────────────────────────────────────

def test_config_loads():
    from config import cfg
    assert cfg is not None


def test_config_defaults():
    from config import cfg
    # Without env vars, live trading must not be considered ready
    if not cfg.RPC_URL:
        assert cfg.is_live_ready() is False


# ── Market data ───────────────────────────────────────────────────────────────

def test_market_data_fallback(monkeypatch):
    """MarketData returns a positive float even when the network is down."""
    import requests
    from engine.market_data import MarketData

    def _bad(*a, **kw):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", _bad)
    price = MarketData().get_price()
    assert isinstance(price, float)
    assert price > 0


# ── Portfolio ─────────────────────────────────────────────────────────────────

def test_portfolio_buy_sell_roundtrip():
    from engine.portfolio import Portfolio
    p = Portfolio(initial_usd=10_000.0)
    assert p.buy(price=2000.0, amount=1.0)
    assert p.balance_eth == 1.0
    assert p.balance_usd == 8_000.0
    assert p.sell(price=2100.0, amount=1.0)
    assert p.balance_eth == 0.0
    assert p.balance_usd == pytest.approx(10_100.0)


# ── Strategy ──────────────────────────────────────────────────────────────────

def test_strategy_signal_type():
    from engine.strategies.mean_reversion import MeanReversionStrategy
    s = MeanReversionStrategy()
    for price in [1800.0, 2000.0, 2200.0]:
        sig = s.signal(price)
        assert sig in ("BUY", "SELL", "HOLD")


# ── Risk manager ──────────────────────────────────────────────────────────────

def test_risk_manager_trade_gate():
    from engine.risk_manager import RiskManager
    r = RiskManager()
    # Fresh manager should allow trading
    assert r.can_trade() is True


# ── Arbitrage scanner ─────────────────────────────────────────────────────────

def test_arbitrage_scanner_scan():
    from engine.arbitrage.arbitrage_scanner import ArbitrageScanner
    # Pass rpc_url=None to force simulation mode (no network calls)
    scanner = ArbitrageScanner(rpc_url=None)
    result = scanner.scan(2000.0)
    # scan() returns a list of opportunities or None when spread < threshold
    assert result is None or isinstance(result, list)


# ── Executor (simulated) ──────────────────────────────────────────────────────

def test_executor_simulated_buy():
    from engine.execution.executor import Executor
    from engine.portfolio import Portfolio
    ex = Executor()
    p = Portfolio(initial_usd=5000.0)
    ok = ex.execute_buy(p, price=2000.0, amount=1.0)
    assert ok is True
    assert p.balance_eth == 1.0


# ── Wallet config (no RPC required) ──────────────────────────────────────────

def test_wallet_config_requires_key():
    from vault.wallet_config import WalletConfig
    with pytest.raises(ValueError, match="PRIVATE_KEY"):
        WalletConfig("", "http://localhost:8545")


def test_wallet_config_requires_rpc():
    from vault.wallet_config import WalletConfig
    with pytest.raises(ValueError, match="RPC_URL"):
        WalletConfig("0" * 64, "")


def test_wallet_config_strips_0x_prefix():
    """WalletConfig should accept keys with or without 0x prefix."""
    from vault.wallet_config import WalletConfig
    # Use a known valid 32-byte private key (all-ones — never use for real funds)
    key_no_prefix = "1" * 64
    key_with_prefix = "0x" + key_no_prefix
    wc1 = WalletConfig(key_no_prefix, "http://localhost:8545")
    wc2 = WalletConfig(key_with_prefix, "http://localhost:8545")
    assert wc1.account.address == wc2.account.address


# ── API health ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_health():
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"


@pytest.mark.asyncio
async def test_api_root():
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_api_status():
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/status")
    assert r.status_code == 200
