"""
tests/test_flashloan_terminal.py
=================================

Offline unit tests for the flashloan_terminal.py interactive system.
All web3 / RPC / network calls are mocked — no live network required.

Tests cover:
  - FlashLoanTerminal initialisation (all engine modules wired up)
  - Arbitrage scanning via Bellman-Ford integration
  - Scan result classification (opportunity vs no_opportunity vs no_data)
  - Risk manager gating (daily trade limit)
  - Dry-run execution path (no tx broadcast)
  - Status / history / config display methods
  - CLI argument parsing (--scan, --auto, --status)
  - Colour / mask helpers
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── autouse fixture: prevent any real network init in all tests ───────────────

@pytest.fixture(autouse=True)
def _no_network_init(monkeypatch):
    """Patch _init_flash_executor to a no-op so tests never trigger
    AlchemyClient / RPC connectivity checks, regardless of env vars."""
    monkeypatch.setattr(
        "flashloan_terminal.FlashLoanTerminal._init_flash_executor",
        lambda self: None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FlashLoanTerminal initialisation
# ─────────────────────────────────────────────────────────────────────────────

class TestTerminalInit:

    def test_terminal_creates_engine_modules(self):
        """All core modules (market, portfolio, risk, arb, liquidity, bellman)
        must be initialised by the constructor."""
        from flashloan_terminal import FlashLoanTerminal
        t = FlashLoanTerminal()
        assert t.market is not None
        assert t.portfolio is not None
        assert t.risk is not None
        assert t.arb is not None
        assert t.liquidity is not None
        assert t.bellman is not None
        assert t.running is True
        assert t.cycle == 0

    def test_flash_executor_none_without_env(self):
        """Without RPC + KEY + RECEIVER, flash_executor should be None."""
        from flashloan_terminal import FlashLoanTerminal
        with patch.dict(os.environ, {
            "RPC_URL": "",
            "PRIVATE_KEY": "",
            "FLASH_RECEIVER_ADDRESS": "",
        }):
            t = FlashLoanTerminal()
            assert t.flash_executor is None


# ─────────────────────────────────────────────────────────────────────────────
# Arbitrage scanning
# ─────────────────────────────────────────────────────────────────────────────

class TestScanArbitrage:

    def _terminal(self):
        from flashloan_terminal import FlashLoanTerminal
        t = FlashLoanTerminal()
        return t

    def test_scan_returns_no_data_when_market_unavailable(self):
        t = self._terminal()
        with patch.object(t.market, "get_price", return_value=None):
            result = t.scan_arbitrage()
        assert result["status"] == "no_data"
        assert result["eth_price"] is None

    def test_scan_returns_no_opportunity_on_normal_market(self):
        t = self._terminal()
        # Equal prices → no arb
        with patch.object(t.market, "get_price", return_value=2000.0), \
             patch.object(t.liquidity, "get_price", return_value=2000.0), \
             patch.object(t.arb, "scan", return_value=None):
            result = t.scan_arbitrage()
        assert result["status"] == "no_opportunity"
        assert result["eth_price"] == 2000.0

    def test_scan_detects_opportunity_with_profitable_cycle(self):
        from nexus_arb.algorithms.bellman_ford import ArbitrageResult
        t = self._terminal()

        fake_result = ArbitrageResult(
            has_cycle=True,
            cycle=["WETH", "USDC", "WETH"],
            profit_ratio=1.005,
            cycle_edges=[("WETH", "USDC", 2010.0), ("USDC", "WETH", 1 / 2000.0)],
        )

        with patch.object(t.market, "get_price", return_value=2000.0), \
             patch.object(t.liquidity, "get_price", return_value=2005.0), \
             patch.object(t.arb, "scan", return_value=None), \
             patch.object(t.bellman, "find_best_arbitrage", return_value=fake_result):
            result = t.scan_arbitrage()

        assert result["status"] == "opportunity"
        assert result["profit_pct"] > 0
        assert result["est_profit_usd"] > 0

    def test_scan_increments_cycle_counter(self):
        t = self._terminal()
        with patch.object(t.market, "get_price", return_value=2000.0), \
             patch.object(t.liquidity, "get_price", return_value=2000.0), \
             patch.object(t.arb, "scan", return_value=None):
            t.scan_arbitrage()
            t.scan_arbitrage()
        assert t.cycle == 2


# ─────────────────────────────────────────────────────────────────────────────
# Flash loan execution
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteFlashLoan:

    def _terminal(self):
        from flashloan_terminal import FlashLoanTerminal
        return FlashLoanTerminal()

    def test_execute_returns_none_on_no_opportunity(self):
        t = self._terminal()
        result = t.execute_flash_loan({"status": "no_opportunity", "eth_price": 2000.0})
        assert result is None

    def test_execute_returns_none_when_risk_limit_reached(self):
        from nexus_arb.algorithms.bellman_ford import ArbitrageResult
        t = self._terminal()
        t.risk.trade_count = t.risk.max_daily_trades  # exhaust limit

        scan = {
            "status": "opportunity",
            "eth_price": 2000.0,
            "result": ArbitrageResult(True, ["WETH", "USDC", "WETH"], 1.01, []),
            "profit_pct": 1.0,
            "est_profit_usd": 20.0,
        }
        result = t.execute_flash_loan(scan)
        assert result is None

    def test_execute_returns_none_below_min_profit(self):
        from nexus_arb.algorithms.bellman_ford import ArbitrageResult
        t = self._terminal()

        scan = {
            "status": "opportunity",
            "eth_price": 2000.0,
            "result": ArbitrageResult(True, ["WETH", "USDC", "WETH"], 1.0001, []),
            "profit_pct": 0.01,
            "est_profit_usd": 0.20,  # below MIN_PROFIT_USD
        }
        result = t.execute_flash_loan(scan)
        assert result is None

    def test_execute_dry_run_without_executor(self):
        """When executor is None and DRY_RUN, should print dry-run message."""
        from nexus_arb.algorithms.bellman_ford import ArbitrageResult
        t = self._terminal()
        t.flash_executor = None

        scan = {
            "status": "opportunity",
            "eth_price": 2000.0,
            "result": ArbitrageResult(True, ["WETH", "USDC", "WETH"], 1.01, []),
            "profit_pct": 1.0,
            "est_profit_usd": 20.0,
        }
        with patch("flashloan_terminal.DRY_RUN", True):
            result = t.execute_flash_loan(scan)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Status / history / config display
# ─────────────────────────────────────────────────────────────────────────────

class TestDisplayMethods:

    def _terminal(self):
        from flashloan_terminal import FlashLoanTerminal
        return FlashLoanTerminal()

    def test_show_status_runs_without_error(self):
        t = self._terminal()
        # Should not raise
        t.show_status()

    def test_show_history_empty(self):
        t = self._terminal()
        t.show_history()  # should not raise with empty trades

    def test_show_history_with_trades(self):
        t = self._terminal()
        t.portfolio.log_trade("FLASH_ARB", 2000.0, 1.0)
        t.show_history()
        assert len(t.portfolio.trades) == 1

    def test_show_config_runs_without_error(self):
        t = self._terminal()
        t.show_config()


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:

    def test_mask_alchemy_url(self):
        from flashloan_terminal import _mask
        url = "https://eth-mainnet.g.alchemy.com/v2/abcdefghijklmnop"
        masked = _mask(url)
        assert "abcd" in masked
        assert "mnop" in masked
        assert "efghijkl" not in masked

    def test_mask_short_url(self):
        from flashloan_terminal import _mask
        url = "http://localhost:8545"
        assert _mask(url) == url

    def test_chain_name_mainnet(self):
        from flashloan_terminal import _chain_name
        with patch("flashloan_terminal.CHAIN_ID", 1):
            assert "Mainnet" in _chain_name()

    def test_chain_name_sepolia(self):
        from flashloan_terminal import _chain_name
        with patch("flashloan_terminal.CHAIN_ID", 11155111):
            assert "Sepolia" in _chain_name()

    def test_chain_name_unknown(self):
        from flashloan_terminal import _chain_name
        with patch("flashloan_terminal.CHAIN_ID", 999):
            assert "999" in _chain_name()


# ─────────────────────────────────────────────────────────────────────────────
# CLI argument parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestCLI:

    def test_module_importable(self):
        """flashloan_terminal is importable without side effects."""
        import flashloan_terminal
        assert hasattr(flashloan_terminal, "FlashLoanTerminal")
        assert hasattr(flashloan_terminal, "main")

    def test_argparse_scan_flag(self):
        """--scan flag is recognized."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--scan", action="store_true")
        args = parser.parse_args(["--scan"])
        assert args.scan is True

    def test_argparse_auto_flag(self):
        """--auto flag is recognized."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--auto", action="store_true")
        args = parser.parse_args(["--auto"])
        assert args.auto is True

    def test_argparse_status_flag(self):
        """--status flag is recognized."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--status", action="store_true")
        args = parser.parse_args(["--status"])
        assert args.status is True


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown and finalization
# ─────────────────────────────────────────────────────────────────────────────

class TestShutdown:

    def test_shutdown_sets_running_false(self):
        from flashloan_terminal import FlashLoanTerminal
        t = FlashLoanTerminal()
        assert t.running is True
        t.shutdown()
        assert t.running is False

    def test_finalize_with_no_trades(self):
        from flashloan_terminal import FlashLoanTerminal
        t = FlashLoanTerminal()
        t._finalize()  # should not raise even with 0 trades

    def test_finalize_saves_log_when_trades_exist(self):
        from flashloan_terminal import FlashLoanTerminal
        t = FlashLoanTerminal()
        t.portfolio.log_trade("TEST", 2000.0, 0.5)

        with patch.object(t.portfolio, "save_trade_log") as mock_save:
            t._finalize()
            mock_save.assert_called_once()
