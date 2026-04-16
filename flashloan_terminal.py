#!/usr/bin/env python3
# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
flashloan_terminal.py — Interactive terminal-based flash loan system.
═══════════════════════════════════════════════════════════════════════

Run locally:
    python flashloan_terminal.py                # interactive menu
    python flashloan_terminal.py --scan         # single scan then exit
    python flashloan_terminal.py --auto         # continuous auto-scan loop
    python flashloan_terminal.py --status       # show config status and exit

Integrates with:
  - nexus_arb/algorithms/bellman_ford.py   (arbitrage cycle detection)
  - nexus_arb/flash_loan_executor.py       (on-chain NexusFlashReceiver)
  - engine/market_data.py                  (ETH/USD price feed)
  - engine/arbitrage/arbitrage_scanner.py  (cross-DEX spread scanner)
  - engine/dex/liquidity_monitor.py        (DEX price feed)
  - engine/portfolio.py                    (trade logging & P&L)
  - engine/risk_manager.py                 (daily trade guard)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

from dotenv import load_dotenv

load_dotenv()

# ── Runtime config (from .env with safe defaults) ────────────────────────────

SCAN_INTERVAL       = int(os.getenv("SCAN_INTERVAL", "30"))
MIN_PROFIT_USD      = float(os.getenv("MIN_PROFIT_USD", "2.0"))
GAS_BUDGET_USD      = float(os.getenv("GAS_BUDGET_USD", "5.0"))
FLASH_LOAN_AMOUNT   = float(os.getenv("FLASH_LOAN_AMOUNT_ETH", "1.0"))
DRY_RUN             = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
CHAIN_ID            = int(os.getenv("CHAIN_ID", "1"))

# ── Imports (all existing modules) ───────────────────────────────────────────

from engine.market_data import MarketData
from engine.portfolio import Portfolio
from engine.risk_manager import RiskManager
from engine.arbitrage.arbitrage_scanner import ArbitrageScanner
from engine.dex.liquidity_monitor import LiquidityMonitor
from nexus_arb.algorithms.bellman_ford import BellmanFordArb


# ── ANSI colour helpers ──────────────────────────────────────────────────────

_BOLD    = "\033[1m"
_GREEN   = "\033[92m"
_RED     = "\033[91m"
_YELLOW  = "\033[93m"
_CYAN    = "\033[96m"
_DIM     = "\033[2m"
_RESET   = "\033[0m"


def _c(text: str, colour: str) -> str:
    return f"{colour}{text}{_RESET}"


# ── Wallet / env helpers ─────────────────────────────────────────────────────

def _load_wallet_address() -> str:
    vault = os.path.join(os.path.dirname(__file__), "vault", "wallet.json")
    if os.path.exists(vault):
        with open(vault) as f:
            return json.load(f)["address"]
    return os.getenv("WALLET_ADDRESS", "NOT CONFIGURED")


def _rpc_url() -> Optional[str]:
    rpc = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
    if rpc:
        return rpc
    key = os.getenv("ALCHEMY_API_KEY")
    if key:
        return f"https://eth-mainnet.g.alchemy.com/v2/{key}"
    return None


def _chain_name() -> str:
    names = {1: "Ethereum Mainnet", 11155111: "Sepolia Testnet", 42161: "Arbitrum One"}
    return names.get(CHAIN_ID, f"Chain {CHAIN_ID}")


def _flash_receiver() -> str:
    return os.getenv("FLASH_RECEIVER_ADDRESS", "NOT SET")


def _has_private_key() -> bool:
    return bool(os.getenv("PRIVATE_KEY"))


# ── Banner ───────────────────────────────────────────────────────────────────

def _banner() -> None:
    print()
    print(_c("  ╔══════════════════════════════════════════════════════════════╗", _CYAN))
    print(_c("  ║         AUREON Flash Loan Terminal  —  Local Host           ║", _CYAN))
    print(_c("  ╚══════════════════════════════════════════════════════════════╝", _CYAN))
    print()
    print(f"  {_c('Wallet', _BOLD)}   : {_load_wallet_address()}")
    print(f"  {_c('Network', _BOLD)}  : {_chain_name()}")
    print(f"  {_c('RPC', _BOLD)}      : {'✓ configured' if _rpc_url() else _c('✗ NOT SET', _RED)}")
    print(f"  {_c('Key', _BOLD)}      : {'✓ loaded' if _has_private_key() else _c('✗ NOT SET', _RED)}")
    print(f"  {_c('Receiver', _BOLD)} : {_flash_receiver()}")
    print(f"  {_c('Borrow', _BOLD)}   : {FLASH_LOAN_AMOUNT} ETH")
    print(f"  {_c('DRY_RUN', _BOLD)}  : {_c('ON — no transactions broadcast', _GREEN) if DRY_RUN else _c('OFF — LIVE MODE', _RED)}")
    print(f"  {_c('Min P&L', _BOLD)}  : ${MIN_PROFIT_USD}  |  Gas budget: ${GAS_BUDGET_USD}")
    print()


# ── Menu ─────────────────────────────────────────────────────────────────────

def _menu() -> None:
    print(_c("  ┌─────────────────────────────────────┐", _DIM))
    print(_c("  │", _DIM) + f"  {_c('1', _BOLD)} Scan for arbitrage opportunities  " + _c("│", _DIM))
    print(_c("  │", _DIM) + f"  {_c('2', _BOLD)} Execute flash loan (single)        " + _c("│", _DIM))
    print(_c("  │", _DIM) + f"  {_c('3', _BOLD)} Auto-scan loop (continuous)        " + _c("│", _DIM))
    print(_c("  │", _DIM) + f"  {_c('4', _BOLD)} Show system status                 " + _c("│", _DIM))
    print(_c("  │", _DIM) + f"  {_c('5', _BOLD)} Show trade history                 " + _c("│", _DIM))
    print(_c("  │", _DIM) + f"  {_c('6', _BOLD)} Show configuration                 " + _c("│", _DIM))
    print(_c("  │", _DIM) + f"  {_c('q', _BOLD)} Quit                               " + _c("│", _DIM))
    print(_c("  └─────────────────────────────────────┘", _DIM))


# ── Core engine ──────────────────────────────────────────────────────────────

class FlashLoanTerminal:
    """Interactive terminal flash loan system integrating all repo components."""

    def __init__(self) -> None:
        self.market     = MarketData()
        self.portfolio  = Portfolio(initial_usd=float(os.getenv("INITIAL_USD", "10000")))
        self.risk       = RiskManager(
            max_daily_trades=int(os.getenv("MAX_DAILY_TRADES", "20")),
            max_position_usd=float(os.getenv("MAX_POSITION_USD", "2000")),
        )
        self.arb        = ArbitrageScanner(rpc_url=_rpc_url())
        self.liquidity  = LiquidityMonitor()
        self.bellman    = BellmanFordArb()
        self.running    = True
        self.cycle      = 0

        # Flash executor is lazy-initialised on first execution request
        # to avoid network calls during scan-only / offline use.
        self._flash_executor_init = False
        self.flash_executor = None

    def _ensure_executor(self) -> None:
        """Lazy-initialise FlashLoanExecutor on first use (avoids network
        calls during scan-only / offline startup)."""
        if self._flash_executor_init:
            return
        self._flash_executor_init = True
        self._init_flash_executor()

    def _init_flash_executor(self) -> None:
        """Try to initialise the production FlashLoanExecutor."""
        rpc = _rpc_url()
        pk = os.getenv("PRIVATE_KEY")
        receiver = os.getenv("FLASH_RECEIVER_ADDRESS")

        if not rpc or not pk or not receiver:
            return

        try:
            from engine.mainnet.alchemy_client import AlchemyClient
            from engine.mainnet.transaction_manager import TransactionManager
            from nexus_arb.flash_loan_executor import FlashLoanExecutor

            client = AlchemyClient(rpc)
            tx_mgr = TransactionManager(
                client=client,
                private_key=pk,
                chain_id=CHAIN_ID,
            )
            self.flash_executor = FlashLoanExecutor.from_env(client.w3, tx_mgr)
            print(_c("  [✓] FlashLoanExecutor initialised", _GREEN))
        except Exception as exc:
            print(f"  {_c('[!]', _YELLOW)} FlashLoanExecutor init failed: {exc}")
            print(f"  {_c('[i]', _DIM)} Scanning still works — execution requires deployed contract")

    # ── Price fetch ──────────────────────────────────────────────────────────

    def _fetch_prices(self) -> tuple:
        """Fetch ETH price from market data and DEX liquidity."""
        eth_price = self.market.get_price()
        dex_price = self.liquidity.get_price()
        return eth_price, dex_price

    # ── Bellman-Ford scan ────────────────────────────────────────────────────

    def scan_arbitrage(self) -> dict:
        """
        Scan for flash loan arbitrage opportunities.

        Returns a dict with scan results including any detected cycles.
        """
        self.cycle += 1
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        print()
        print(f"  {_c('── Scan', _BOLD)} #{self.cycle:>4}  [{ts} UTC] {'─' * 30}")

        eth_price, dex_price = self._fetch_prices()

        if eth_price is None:
            print(f"  {_c('[WARN]', _YELLOW)} Market data unavailable")
            return {"status": "no_data", "eth_price": None}

        print(f"  ETH/USD  : {_c(f'${eth_price:,.2f}', _BOLD)}")

        if dex_price is not None:
            print(f"  DEX price: {_c(f'${dex_price:,.2f}', _BOLD)}")

        # Populate Bellman-Ford graph
        self.bellman.clear()

        if dex_price and eth_price:
            self.bellman.add_edge("WETH", "USDC", dex_price, "uniswap_v3")
            self.bellman.add_edge("USDC", "WETH", 1.0 / dex_price, "uniswap_v3")

            # Add cross-DEX edges from ArbitrageScanner
            opps = self.arb.scan(eth_price)
            if opps:
                for o in opps:
                    spread = o.get("spread_pct", 0)
                    if spread > 0.1 and o.get("sell_price") and o.get("buy_price"):
                        sell_dex = o.get("sell_on", "sushiswap")
                        buy_dex = o.get("buy_on", "uniswap_v3")
                        self.bellman.add_edge("WETH", "USDC", o["sell_price"], sell_dex)
                        self.bellman.add_edge("USDC", "WETH", 1.0 / o["buy_price"], buy_dex)
                        print(f"  {_c('DEX spread', _CYAN)}: {o['buy_on']} → {o['sell_on']} "
                              f"({o['spread_pct']:.3f}%)")

        # Run Bellman-Ford
        result = self.bellman.find_best_arbitrage()

        if result.has_cycle and result.profit_ratio > 1.0:
            profit_pct = (result.profit_ratio - 1.0) * 100.0
            est_profit_usd = profit_pct / 100.0 * FLASH_LOAN_AMOUNT * (eth_price or 0)

            print()
            print(f"  {_c('⚡ ARBITRAGE DETECTED', _GREEN)}")
            print(f"  Cycle        : {' → '.join(result.cycle)}")
            print(f"  Profit ratio : {result.profit_ratio:.6f}")
            print(f"  Est. profit  : {_c(f'{profit_pct:.4f}%', _GREEN)} "
                  f"(~${est_profit_usd:.2f} on {FLASH_LOAN_AMOUNT} ETH)")

            return {
                "status": "opportunity",
                "eth_price": eth_price,
                "result": result,
                "profit_pct": profit_pct,
                "est_profit_usd": est_profit_usd,
            }
        else:
            print(f"  {_c('No profitable cycle detected', _DIM)}")
            return {"status": "no_opportunity", "eth_price": eth_price}

    # ── Execute flash loan ───────────────────────────────────────────────────

    def execute_flash_loan(self, scan_result: Optional[dict] = None) -> Optional[str]:
        """
        Execute a flash loan based on a scan result.

        If no scan_result is provided, runs a fresh scan first.
        Returns the tx hash on success, None otherwise.
        """
        if scan_result is None:
            scan_result = self.scan_arbitrage()

        if scan_result["status"] != "opportunity":
            print(f"\n  {_c('[i]', _DIM)} No profitable opportunity to execute")
            return None

        result = scan_result["result"]
        eth_price = scan_result["eth_price"]
        est_profit = scan_result["est_profit_usd"]

        # Risk checks
        if not self.risk.can_trade():
            print(f"\n  {_c('[RISK]', _YELLOW)} Daily trade limit reached — cannot execute")
            return None

        if est_profit < MIN_PROFIT_USD:
            print(f"\n  {_c('[SKIP]', _YELLOW)} Est. profit ${est_profit:.2f} < min ${MIN_PROFIT_USD}")
            return None

        # Executor check (lazy-init on first execution request)
        self._ensure_executor()
        if self.flash_executor is None:
            print(f"\n  {_c('[!]', _YELLOW)} FlashLoanExecutor not available")
            print(f"  {_c('[i]', _DIM)} Requires: RPC_URL, PRIVATE_KEY, FLASH_RECEIVER_ADDRESS")
            if DRY_RUN:
                print(f"  {_c('[DRY RUN]', _GREEN)} Would execute: "
                      f"borrow={FLASH_LOAN_AMOUNT} ETH  "
                      f"cycle={' → '.join(result.cycle)}")
            return None

        # Confirmation for live mode
        if not DRY_RUN:
            print()
            print(f"  {_c('*** LIVE MODE — real funds will be used ***', _RED)}")
            print(f"  Borrow: {FLASH_LOAN_AMOUNT} ETH  |  Est. profit: ${est_profit:.2f}")
            confirm = input(f"  {_c('Type YES to confirm:', _BOLD)} ").strip()
            if confirm != "YES":
                print("  Aborted.")
                return None

        # Execute
        print(f"\n  {_c('Executing flash loan…', _CYAN)}")
        try:
            receipt = self.flash_executor.execute_from_result(
                result,
                borrow_amount_eth=FLASH_LOAN_AMOUNT,
                dry_run=DRY_RUN,
            )

            if receipt and hasattr(receipt, "tx_hash"):
                self.portfolio.log_trade("FLASH_ARB", eth_price, FLASH_LOAN_AMOUNT, receipt.tx_hash)
                self.risk.record_trade()
                tx_short = receipt.tx_hash[:18] + "…"
                print(f"  {_c('✓ TX confirmed', _GREEN)}: {tx_short}  block={receipt.block_number}")
                return receipt.tx_hash
            elif DRY_RUN:
                print(f"  {_c('[DRY RUN]', _GREEN)} TX built but not broadcast")
                return None
        except Exception as exc:
            print(f"  {_c('[ERROR]', _RED)} Flash loan execution failed: {exc}")
            return None

        return None

    # ── Auto-scan loop ───────────────────────────────────────────────────────

    def auto_scan(self) -> None:
        """Continuous scan loop — scans and optionally executes until stopped."""
        print(f"\n  {_c('Starting auto-scan loop', _CYAN)} (Ctrl+C to stop)")
        print(f"  Interval: {SCAN_INTERVAL}s  |  DRY_RUN={DRY_RUN}")
        print()

        while self.running:
            scan = self.scan_arbitrage()

            if scan["status"] == "opportunity":
                self.execute_flash_loan(scan)

            # Portfolio summary
            summary = self.portfolio.summary()
            print(f"  {_c('PORTFOLIO', _BOLD)}: ${summary['balance_usd']:,.2f} USD  "
                  f"{summary['balance_eth']:.4f} ETH  "
                  f"P&L: ${summary['pnl_usd']:+.2f}  "
                  f"Trades: {summary['trade_count']}")
            print()

            for _ in range(SCAN_INTERVAL, 0, -1):
                if not self.running:
                    break
                time.sleep(1)

        self._finalize()

    # ── Status display ───────────────────────────────────────────────────────

    def show_status(self) -> None:
        """Display full system status."""
        print()
        print(f"  {_c('═══ System Status ═══', _BOLD)}")
        print()

        # Configuration readiness
        rpc_ok = _rpc_url() is not None
        key_ok = _has_private_key()
        recv_ok = os.getenv("FLASH_RECEIVER_ADDRESS", "") != ""
        self._ensure_executor()
        exec_ok = self.flash_executor is not None

        checks = [
            ("RPC endpoint", rpc_ok),
            ("Private key", key_ok),
            ("Flash receiver contract", recv_ok),
            ("Flash executor ready", exec_ok),
            ("Risk manager (can trade)", self.risk.can_trade()),
        ]

        for label, ok in checks:
            icon = _c("✓", _GREEN) if ok else _c("✗", _RED)
            print(f"    {icon} {label}")

        print()

        # Prices
        eth_price = self.market.get_price()
        dex_price = self.liquidity.get_price()
        print(f"  {_c('Market', _BOLD)}")
        print(f"    ETH/USD (CoinGecko) : ${eth_price:,.2f}" if eth_price else
              "    ETH/USD (CoinGecko) : unavailable")
        print(f"    ETH/USD (DEX)       : ${dex_price:,.2f}" if dex_price else
              "    ETH/USD (DEX)       : unavailable")

        print()

        # Portfolio
        summary = self.portfolio.summary()
        print(f"  {_c('Portfolio', _BOLD)}")
        print(f"    USD balance : ${summary['balance_usd']:,.2f}")
        print(f"    ETH balance : {summary['balance_eth']:.6f}")
        print(f"    Total P&L   : ${summary['pnl_usd']:+.2f} ({summary['pnl_pct']:+.3f}%)")
        print(f"    Trades      : {summary['trade_count']}")

        print()

        # On-chain profit (if executor available)
        if self.flash_executor:
            try:
                on_chain_profit = self.flash_executor.total_profit_eth()
                print(f"  {_c('On-chain', _BOLD)}")
                print("    Total profit (contract): %.6f ETH" % on_chain_profit)
                print()
            except Exception:
                pass

    # ── Trade history ────────────────────────────────────────────────────────

    def show_history(self) -> None:
        """Display trade history."""
        print()
        if not self.portfolio.trades:
            print(f"  {_c('No trades recorded yet', _DIM)}")
            return

        print(f"  {_c('═══ Trade History ═══', _BOLD)}")
        print()
        print(f"  {'#':>3}  {'Time':>20}  {'Side':>10}  {'Price':>10}  "
              f"{'Amount':>8}  {'Value':>10}  {'TX Hash':>20}")
        print(f"  {'─' * 3}  {'─' * 20}  {'─' * 10}  {'─' * 10}  "
              f"{'─' * 8}  {'─' * 10}  {'─' * 20}")

        for i, t in enumerate(self.portfolio.trades, 1):
            ts = t.get("timestamp", "")[:19]
            side = t.get("side", "?")
            price = t.get("price_usd", 0)
            amount = t.get("amount_eth", 0)
            value = t.get("value_usd", 0)
            tx = t.get("tx_hash") or "—"
            if len(tx) > 20:
                tx = tx[:17] + "…"
            print(f"  {i:>3}  {ts:>20}  {side:>10}  ${price:>9,.2f}  "
                  f"{amount:>8.4f}  ${value:>9,.2f}  {tx:>20}")
        print()

    # ── Configuration display ────────────────────────────────────────────────

    def show_config(self) -> None:
        """Display current configuration."""
        print()
        print(f"  {_c('═══ Configuration ═══', _BOLD)}")
        print()

        configs = [
            ("Network", _chain_name()),
            ("RPC URL", _mask(_rpc_url() or "NOT SET")),
            ("Wallet", _load_wallet_address()),
            ("Private Key", "✓ loaded" if _has_private_key() else "NOT SET"),
            ("Flash Receiver", _flash_receiver()),
            ("", ""),
            ("Borrow Amount", f"{FLASH_LOAN_AMOUNT} ETH"),
            ("DRY_RUN", str(DRY_RUN)),
            ("Min Profit", f"${MIN_PROFIT_USD}"),
            ("Gas Budget", f"${GAS_BUDGET_USD}"),
            ("Scan Interval", f"{SCAN_INTERVAL}s"),
            ("Max Daily Trades", str(int(os.getenv("MAX_DAILY_TRADES", "20")))),
        ]

        for label, value in configs:
            if not label:
                print()
                continue
            print(f"    {label:<18}: {value}")
        print()

    # ── Finalize ─────────────────────────────────────────────────────────────

    def _finalize(self) -> None:
        """Save trade log and print final summary."""
        print()
        print(f"  {_c('── Final Summary ──', _BOLD)}")
        final = self.portfolio.summary()
        print(f"    USD balance : ${final['balance_usd']:,.2f}")
        print(f"    ETH balance : {final['balance_eth']:.6f}")
        print(f"    Total P&L   : ${final['pnl_usd']:+.2f}")
        print(f"    Trades      : {final['trade_count']}")
        print()

        if self.portfolio.trades:
            self.portfolio.save_trade_log()
            print("  Trade log saved → vault/trade_log.json")

    # ── Shutdown ─────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        self.running = False


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mask(url: str) -> str:
    """Mask API keys in URLs for display."""
    if "alchemy.com" in url and "/v2/" in url:
        parts = url.split("/v2/")
        key = parts[1] if len(parts) > 1 else ""
        if len(key) > 8:
            return f"{parts[0]}/v2/{key[:4]}…{key[-4:]}"
    if len(url) > 60:
        return url[:57] + "…"
    return url


# ── Interactive loop ─────────────────────────────────────────────────────────

def interactive(terminal: FlashLoanTerminal) -> None:
    """Run the interactive menu loop."""

    def _shutdown(sig, frame):
        terminal.shutdown()
        print(f"\n  {_c('Shutting down…', _DIM)}")

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while terminal.running:
        _menu()
        try:
            choice = input(f"\n  {_c('>', _BOLD)} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "1":
            terminal.scan_arbitrage()
        elif choice == "2":
            terminal.execute_flash_loan()
        elif choice == "3":
            terminal.auto_scan()
        elif choice == "4":
            terminal.show_status()
        elif choice == "5":
            terminal.show_history()
        elif choice == "6":
            terminal.show_config()
        elif choice in ("q", "quit", "exit"):
            break
        else:
            print(f"  {_c('Unknown option', _YELLOW)} — enter 1-6 or q")

        print()

    terminal._finalize()
    print(f"\n  {_c('Goodbye!', _CYAN)}\n")


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AUREON Flash Loan Terminal — interactive flash loan system",
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Run a single arbitrage scan and exit",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Continuous auto-scan loop (like trade.py --flash)",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show system status and exit",
    )
    args = parser.parse_args()

    _banner()
    terminal = FlashLoanTerminal()

    if args.status:
        terminal.show_status()
    elif args.scan:
        terminal.scan_arbitrage()
    elif args.auto:
        signal.signal(signal.SIGINT, lambda s, f: terminal.shutdown())
        signal.signal(signal.SIGTERM, lambda s, f: terminal.shutdown())
        terminal.auto_scan()
    else:
        interactive(terminal)


if __name__ == "__main__":
    main()
