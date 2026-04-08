"""
Tests for the AUREON trading engine.
All tests run offline — no RPC or network calls required.
"""

import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Market data ───────────────────────────────────────────────────────────────

class TestMarketData:
    def test_import(self):
        from engine.market_data import MarketData
        m = MarketData()
        assert m.FALLBACK_PRICE == 2000.0

    def test_fallback_on_network_error(self, monkeypatch):
        from engine.market_data import MarketData
        import requests

        def _bad_get(*a, **kw):
            raise requests.ConnectionError("offline")

        monkeypatch.setattr(requests, "get", _bad_get)
        price = MarketData().get_price()
        assert price == MarketData.FALLBACK_PRICE


# ── Portfolio ─────────────────────────────────────────────────────────────────

class TestPortfolio:
    def test_initial_state(self):
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=5000.0)
        assert p.balance_usd == 5000.0
        assert p.balance_eth == 0.0
        assert len(p.trades) == 0

    def test_buy_deducts_usd(self):
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=10_000.0)
        ok = p.buy(price=2000.0, amount=1.0)
        assert ok is True
        assert p.balance_usd == 8_000.0
        assert p.balance_eth == 1.0

    def test_buy_insufficient_funds(self):
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=100.0)
        ok = p.buy(price=2000.0, amount=1.0)
        assert ok is False
        assert p.balance_usd == 100.0

    def test_sell(self):
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=10_000.0)
        p.buy(price=2000.0, amount=1.0)
        ok = p.sell(price=2100.0, amount=1.0)
        assert ok is True
        assert p.balance_eth == 0.0
        assert abs(p.balance_usd - 10_100.0) < 0.01

    def test_sell_insufficient_eth(self):
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=10_000.0)
        ok = p.sell(price=2000.0, amount=1.0)
        assert ok is False

    def test_summary_keys(self):
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=10_000.0)
        s = p.summary()
        for key in ("balance_usd", "balance_eth", "total_value", "pnl_usd", "pnl_pct", "trade_count"):
            assert key in s

    def test_log_trade_paper(self):
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=10_000.0)
        p.log_trade("BUY", 2000.0, 0.5)   # no tx_hash → paper mode
        assert len(p.trades) == 1
        assert p.balance_eth == 0.5

    def test_log_trade_live(self):
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=10_000.0)
        # Live mode: balance NOT auto-updated (settled on-chain)
        p.log_trade("BUY", 2000.0, 0.5, tx_hash="0xabc")
        assert len(p.trades) == 1
        assert p.balance_eth == 0.0   # unchanged — live settlement


# ── Risk manager ──────────────────────────────────────────────────────────────

class TestRiskManager:
    def test_can_trade_initially(self):
        from engine.risk_manager import RiskManager
        r = RiskManager(max_daily_trades=5)
        assert r.can_trade() is True

    def test_blocks_after_limit(self):
        from engine.risk_manager import RiskManager
        r = RiskManager(max_daily_trades=3)
        for _ in range(3):
            r.record_trade()
        assert r.can_trade() is False

    def test_reset(self):
        from engine.risk_manager import RiskManager
        r = RiskManager(max_daily_trades=3)
        for _ in range(3):
            r.record_trade()
        r.reset()
        assert r.can_trade() is True


# ── Mean-reversion strategy ───────────────────────────────────────────────────

class TestMeanReversionStrategy:
    def test_hold_until_window_full(self):
        from engine.strategies.mean_reversion import MeanReversionStrategy
        s = MeanReversionStrategy(window=3, threshold=0.02)
        assert s.signal(100.0) == "HOLD"
        assert s.signal(100.0) == "HOLD"

    def test_buy_signal_below_mean(self):
        from engine.strategies.mean_reversion import MeanReversionStrategy
        s = MeanReversionStrategy(window=3, threshold=0.02)
        s.signal(100.0)
        s.signal(100.0)
        # Price drops well below mean
        sig = s.signal(95.0)
        assert sig == "BUY"

    def test_sell_signal_above_mean(self):
        from engine.strategies.mean_reversion import MeanReversionStrategy
        s = MeanReversionStrategy(window=3, threshold=0.02)
        s.signal(100.0)
        s.signal(100.0)
        # Price surges well above mean
        sig = s.signal(105.0)
        assert sig == "SELL"

    def test_hold_within_threshold(self):
        from engine.strategies.mean_reversion import MeanReversionStrategy
        s = MeanReversionStrategy(window=3, threshold=0.02)
        s.signal(100.0)
        s.signal(100.0)
        sig = s.signal(100.5)   # only 0.3 % deviation
        assert sig == "HOLD"


# ── Arbitrage scanner ─────────────────────────────────────────────────────────

class TestArbitrageScanner:
    def test_simulation_mode(self):
        """Scanner works without any RPC (simulation mode)."""
        from engine.arbitrage.arbitrage_scanner import ArbitrageScanner
        arb = ArbitrageScanner(rpc_url=None)
        prices = arb.get_prices()
        assert len(prices) >= 2
        for name, price in prices.items():
            assert isinstance(price, float)
            assert price > 0

    def test_scan_returns_list_or_none(self):
        from engine.arbitrage.arbitrage_scanner import ArbitrageScanner
        arb = ArbitrageScanner(rpc_url=None)
        result = arb.scan(price=2000.0)
        assert result is None or isinstance(result, list)

    def test_opportunity_structure(self):
        """Force a spread by monkey-patching get_prices."""
        from engine.arbitrage.arbitrage_scanner import ArbitrageScanner
        arb = ArbitrageScanner(rpc_url=None, spread_threshold=0.001)

        arb.get_prices = lambda: {
            "uniswap_v3": 2020.0,
            "sushiswap":  2000.0,
        }
        opps = arb.scan()
        assert opps is not None
        opp = opps[0]
        assert opp["buy_on"] == "sushiswap"
        assert opp["sell_on"] == "uniswap_v3"
        assert "spread_pct" in opp
        assert "est_profit_pct" in opp

    def test_sentinel_vs_none(self):
        """rpc_url=_UNSET reads from env; rpc_url=None means simulation."""
        from engine.arbitrage.arbitrage_scanner import ArbitrageScanner
        # Explicit None → simulation, should not raise
        arb = ArbitrageScanner(rpc_url=None)
        assert arb._uni is None
        assert arb._sushi is None


# ── Executor (paper) ──────────────────────────────────────────────────────────

class TestExecutor:
    def test_execute_buy(self):
        from engine.execution.executor import Executor
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=10_000.0)
        ex = Executor()
        ok = ex.execute_buy(p, price=2000.0, amount=0.5)
        assert ok is True
        assert p.balance_eth == 0.5

    def test_execute_sell(self):
        from engine.execution.executor import Executor
        from engine.portfolio import Portfolio
        p = Portfolio(initial_usd=10_000.0)
        p.buy(price=2000.0, amount=1.0)
        ex = Executor()
        ok = ex.execute_sell(p, price=2100.0, amount=1.0)
        assert ok is True
        assert p.balance_eth == 0.0


# ── Wallet config ─────────────────────────────────────────────────────────────

class TestWalletConfig:
    def test_import(self):
        from vault.wallet_config import WalletConfig
        assert WalletConfig is not None

    def test_missing_key_raises(self):
        from vault.wallet_config import WalletConfig
        import pytest
        with pytest.raises(ValueError):
            WalletConfig(private_key="", rpc_url="http://localhost")

    def test_missing_rpc_raises(self):
        from vault.wallet_config import WalletConfig
        import pytest
        with pytest.raises(ValueError):
            WalletConfig(
                private_key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
                rpc_url="",
            )
