#!/usr/bin/env python3
# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
AUREON Trading Bot — main entry point
──────────────────────────────────────
Modes
  --paper   Simulated trading (default) — no real transactions sent
  --live    Live mainnet trading — real ETH spent
  --flash   Live flash loan arbitrage via NexusFlashReceiver (requires deployment)

Quick start:
  python setup_wallet.py       # one-time wallet setup
  python trade.py              # paper-trade (safe, no real funds)
  python trade.py --live       # live mainnet trading (SwapExecutor)
  python trade.py --flash      # live flash loan arbitrage (NexusFlashReceiver)
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

# ── uvloop: faster event loop for any async calls invoked from trade.py ───────
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("aureon.trade")

# ── runtime config ────────────────────────────────────────────────────────────

TRADE_INTERVAL  = int(os.getenv("SCAN_INTERVAL", "30"))   # seconds between cycles
MIN_PROFIT_USD  = float(os.getenv("MIN_PROFIT_USD", "2.0"))
GAS_BUDGET_USD  = float(os.getenv("GAS_BUDGET_USD", "5.0"))
TRADE_SIZE_ETH  = float(os.getenv("TRADE_SIZE_ETH", "0.05"))  # ETH per trade
FLASH_LOAN_AMOUNT_ETH  = float(os.getenv("FLASH_LOAN_AMOUNT_ETH", "1.0"))  # borrow size
DRY_RUN         = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")

# ── imports ───────────────────────────────────────────────────────────────────

from engine.market_data            import MarketData
from engine.portfolio              import Portfolio
from engine.risk_manager           import RiskManager
from engine.strategies.mean_reversion import MeanReversionStrategy
from engine.arbitrage.arbitrage_scanner import ArbitrageScanner
from engine.dex.liquidity_monitor  import LiquidityMonitor
from engine.execution.executor     import Executor


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_wallet_address() -> str:
    vault = os.path.join(os.path.dirname(__file__), "vault", "wallet.json")
    if os.path.exists(vault):
        with open(vault) as f:
            return json.load(f)["address"]
    return os.getenv("WALLET_ADDRESS", "NOT CONFIGURED")


def _banner(live: bool, flash: bool = False) -> None:
    if flash:
        mode = "FLASH LOAN ARB"
    elif live:
        mode = "LIVE MAINNET"
    else:
        mode = "PAPER TRADING"
    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print(f"  ║  AUREON Trading Bot  —  {mode:<24}║")
    print("  ╚══════════════════════════════════════════════════╝")
    print(f"  Wallet  : {_load_wallet_address()}")
    print(f"  Interval: {TRADE_INTERVAL}s  |  Trade size: {TRADE_SIZE_ETH} ETH")
    print(f"  Min profit: ${MIN_PROFIT_USD}  |  Gas budget: ${GAS_BUDGET_USD}")
    if flash:
        print(f"  Flash borrow: {FLASH_LOAN_AMOUNT_ETH} ETH  |  DRY_RUN={DRY_RUN}")
    print()
    if live or flash:
        print("  *** LIVE MODE — real funds will be used ***")
        confirm = input("  Type YES to confirm: ").strip()
        if confirm != "YES":
            print("  Aborted.")
            sys.exit(0)
    print()


# ── main loop ─────────────────────────────────────────────────────────────────

def run(live: bool = False, flash: bool = False) -> None:
    _banner(live, flash)

    rpc_url = os.getenv("RPC_URL") or os.getenv("ETH_RPC")

    # Initialise engine modules
    market    = MarketData()
    portfolio = Portfolio(initial_usd=float(os.getenv("INITIAL_USD", "10000")))
    risk      = RiskManager(
        max_daily_trades=int(os.getenv("MAX_DAILY_TRADES", "20")),
        max_position_usd=float(os.getenv("MAX_POSITION_USD", "2000")),
    )
    strategy  = MeanReversionStrategy(
        window=int(os.getenv("STRATEGY_WINDOW", "12")),
        threshold=float(os.getenv("STRATEGY_THRESHOLD", "0.015")),
    )
    arb       = ArbitrageScanner(rpc_url=rpc_url)
    liquidity = LiquidityMonitor()

    # ── Executor setup ────────────────────────────────────────────────────────
    flash_executor = None
    executor       = None
    sim_executor   = None

    if flash or live:
        from vault.wallet_config import WalletConfig
        if not rpc_url:
            print("  ERROR: RPC_URL not set in .env")
            sys.exit(1)
        private_key = os.getenv("PRIVATE_KEY")
        if not private_key:
            print("  ERROR: PRIVATE_KEY not set — run setup_wallet.py first")
            sys.exit(1)
        wallet = WalletConfig(private_key, rpc_url)
        print(f"  [LIVE] Wallet connected: {wallet.address}")

    if flash:
        # Flash loan mode — uses NexusFlashReceiver + Bellman-Ford
        from web3 import Web3
        from engine.mainnet.alchemy_client import AlchemyClient
        from engine.mainnet.transaction_manager import TransactionManager
        from nexus_arb.flash_loan_executor import FlashLoanExecutor
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb

        alchemy_client  = AlchemyClient(rpc_url)
        w3_flash        = alchemy_client.w3
        tx_manager      = TransactionManager(
            client=alchemy_client,
            private_key=wallet.private_key,
            chain_id=int(os.getenv("CHAIN_ID", "1")),
        )
        flash_executor  = FlashLoanExecutor.from_env(w3_flash, tx_manager)
        bellman_ford    = BellmanFordArb()
        print(f"  [FLASH] NexusFlashReceiver at {os.getenv('FLASH_RECEIVER_ADDRESS', 'NOT SET')}")
        print(f"  [FLASH] DRY_RUN={DRY_RUN}  borrow={FLASH_LOAN_AMOUNT_ETH} ETH")

    elif live:
        from engine.execution.swap_executor import SwapExecutor
        executor = SwapExecutor(wallet, rpc_url)

    else:
        sim_executor = Executor()

    cycle = 0
    running = True

    def _shutdown(sig, frame):
        nonlocal running
        print("\n  Shutting down …")
        running = False

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("  Starting trading loop. Press Ctrl+C to stop.\n")

    while running:
        cycle += 1
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  ── Cycle {cycle:>4}  [{ts} UTC] {'─' * 34}")

        # 1. Fetch ETH price
        eth_price = market.get_price()
        if eth_price is None:
            print("  [WARN] Market data unavailable — skipping cycle")
            time.sleep(TRADE_INTERVAL)
            continue
        print(f"  ETH/USD  : ${eth_price:,.2f}")

        # 2. DEX liquidity check
        dex_price = liquidity.get_price()
        if dex_price is None:
            print("  [WARN] DEX price unavailable — skipping cycle")
            time.sleep(TRADE_INTERVAL)
            continue

        # 3. Flash loan arbitrage (Bellman-Ford + NexusFlashReceiver)
        if flash and flash_executor:
            # Populate BellmanFordArb graph from live DEX prices
            bellman_ford.clear()
            if dex_price and eth_price:
                bellman_ford.add_edge("WETH", "USDC", dex_price,         "uniswap_v3")
                bellman_ford.add_edge("USDC", "WETH", 1.0 / dex_price,   "uniswap_v3")
                # Cross-DEX edges from ArbitrageScanner
                simple_opps = arb.scan(eth_price)
                for o in simple_opps:
                    spread = o.get("spread_pct", 0)
                    if spread > 0.1 and o.get("sell_price") and o.get("buy_price"):
                        sell_dex = o.get("sell_on", "sushiswap")
                        buy_dex  = o.get("buy_on",  "uniswap_v3")
                        bellman_ford.add_edge("WETH", "USDC", o["sell_price"],       sell_dex)
                        bellman_ford.add_edge("USDC", "WETH", 1.0 / o["buy_price"],  buy_dex)

            nexus_result = bellman_ford.find_best_arbitrage()
            if nexus_result.has_cycle and nexus_result.profit_ratio > 1.0:
                est_profit_pct = (nexus_result.profit_ratio - 1.0) * 100.0
                est_profit_usd = est_profit_pct / 100.0 * FLASH_LOAN_AMOUNT_ETH * eth_price
                print(f"  FLASH ARB: cycle={nexus_result.cycle} profit={est_profit_pct:.3f}%  est ${est_profit_usd:.2f}")
                if est_profit_usd >= MIN_PROFIT_USD and risk.can_trade():
                    try:
                        receipt = flash_executor.execute_from_result(
                            nexus_result,
                            borrow_amount_eth=FLASH_LOAN_AMOUNT_ETH,
                            dry_run=DRY_RUN,
                        )
                        if receipt and hasattr(receipt, "tx_hash"):
                            portfolio.log_trade("FLASH_ARB", eth_price, FLASH_LOAN_AMOUNT_ETH, receipt.tx_hash)
                            risk.record_trade()
                            print(f"  [FLASH] tx={receipt.tx_hash[:18]}…  block={receipt.block_number}")
                        elif DRY_RUN:
                            print("  [FLASH] dry_run — tx not broadcast")
                    except Exception as e:
                        print(f"  [ERROR] Flash loan failed: {e}")
            else:
                print("  FLASH ARB: no profitable cycle detected this cycle")

        # 4. Arbitrage scan (simple cross-DEX for live/paper modes)
        elif not flash:
            opps = arb.scan(eth_price)
            if opps:
                opp = opps[0]
                est_profit = opp["est_profit_pct"] / 100 * TRADE_SIZE_ETH * eth_price
                print(f"  ARB OPP  : buy {opp['buy_on']} @ ${opp['buy_price']:.2f} "
                      f"→ sell {opp['sell_on']} @ ${opp['sell_price']:.2f} "
                      f"| spread {opp['spread_pct']:.3f}% | est ${est_profit:.2f}")

                if est_profit >= MIN_PROFIT_USD and risk.can_trade():
                    if live:
                        gas_cost = executor.estimate_gas_usd()
                        if gas_cost <= GAS_BUDGET_USD:
                            try:
                                print(f"  [EXEC] Swapping {TRADE_SIZE_ETH} ETH → USDC …")
                                tx = executor.swap_eth_to_usdc(
                                    amount_eth=TRADE_SIZE_ETH,
                                    slippage=0.005,
                                    expected_usdc=opp["buy_price"] * TRADE_SIZE_ETH,
                                )
                                portfolio.log_trade("ARB_BUY", eth_price, TRADE_SIZE_ETH, tx)
                                risk.record_trade()
                            except Exception as e:
                                print(f"  [ERROR] Swap failed: {e}")
                        else:
                            print(f"  [SKIP] Gas too high: ${gas_cost:.2f} > budget ${GAS_BUDGET_USD}")
                    else:
                        sim_executor.execute_buy(portfolio, eth_price, TRADE_SIZE_ETH)
                        risk.record_trade()
            else:
                # 5. Strategy signal (mean-reversion)
                signal_val = strategy.signal(eth_price)
                print(f"  SIGNAL   : {signal_val}")

                if not risk.can_trade():
                    print("  [RISK] Daily trade limit reached — holding")
                elif signal_val == "BUY" and portfolio.balance_usd >= eth_price * TRADE_SIZE_ETH:
                    if live:
                        gas_cost = executor.estimate_gas_usd() if hasattr(executor, "estimate_gas_usd") else 0
                        if gas_cost <= GAS_BUDGET_USD:
                            try:
                                tx = executor.swap_eth_to_usdc(
                                    amount_eth=TRADE_SIZE_ETH,
                                    slippage=0.005,
                                    expected_usdc=eth_price * TRADE_SIZE_ETH * 0.995,
                                )
                                portfolio.log_trade("BUY", eth_price, TRADE_SIZE_ETH, tx)
                                risk.record_trade()
                            except Exception as e:
                                print(f"  [ERROR] Buy failed: {e}")
                    else:
                        sim_executor.execute_buy(portfolio, eth_price, TRADE_SIZE_ETH)
                        risk.record_trade()

                elif signal_val == "SELL" and portfolio.balance_eth >= TRADE_SIZE_ETH:
                    if live:
                        usdc_amount = eth_price * TRADE_SIZE_ETH
                        try:
                            tx = executor.swap_usdc_to_eth(
                                amount_usdc=usdc_amount,
                                slippage=0.005,
                                expected_eth=TRADE_SIZE_ETH * 0.995,
                            )
                            portfolio.log_trade("SELL", eth_price, TRADE_SIZE_ETH, tx)
                            risk.record_trade()
                        except Exception as e:
                            print(f"  [ERROR] Sell failed: {e}")
                    else:
                        sim_executor.execute_sell(portfolio, eth_price, TRADE_SIZE_ETH)
                        risk.record_trade()

        # 5. Portfolio summary
        summary = portfolio.summary()
        print(f"  PORTFOLIO: ${summary['balance_usd']:,.2f} USD  "
              f"{summary['balance_eth']:.4f} ETH  "
              f"P&L: ${summary['pnl_usd']:+.2f}")
        print()
        time.sleep(TRADE_INTERVAL)

    # Final report
    print()
    print("  ── Final Portfolio ─────────────────────────────────")
    final = portfolio.summary()
    print(f"  USD balance : ${final['balance_usd']:,.2f}")
    print(f"  ETH balance : {final['balance_eth']:.6f}")
    print(f"  Total P&L   : ${final['pnl_usd']:+.2f}")
    print(f"  Trades      : {final['trade_count']}")
    print()
    portfolio.save_trade_log()
    print("  Trade log saved → vault/trade_log.json")
    print()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AUREON Trading Bot")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live mainnet mode (real funds). Default is paper trading.",
    )
    parser.add_argument(
        "--flash",
        action="store_true",
        help="Run in flash loan arbitrage mode via NexusFlashReceiver (requires FLASH_RECEIVER_ADDRESS).",
    )
    args = parser.parse_args()
    run(live=args.live, flash=args.flash)
