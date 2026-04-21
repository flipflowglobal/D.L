#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OnTheDL Autonomous Trading Engine
Fully integrated: MarketData, Portfolio, RiskManager, Strategy, Arbitrage, Liquidity, Executor
Safe for testnet / simulated trading
"""

from __future__ import annotations

import os
import time

from engine.market_data import MarketData
from engine.portfolio import Portfolio
from engine.risk_manager import RiskManager
from engine.strategies.mean_reversion import MeanReversionStrategy
from engine.arbitrage.arbitrage_scanner import ArbitrageScanner
from engine.dex.liquidity_monitor import LiquidityMonitor
from engine.execution.executor import Executor

TRADE_INTERVAL = int(os.getenv("SCAN_INTERVAL", "5"))  # seconds between trade cycles

def run():
    # Initialize modules
    market = MarketData()
    portfolio = Portfolio()
    risk = RiskManager()
    strategy = MeanReversionStrategy()
    arb = ArbitrageScanner()
    liquidity = LiquidityMonitor()
    executor = Executor()

    print("OnTheDL Autonomous Trading Engine started")

    try:
        while True:
            # Get market price
            price = market.get_price()
            print(f"ETH price {price:.2f} USD")

            # Strategy signal
            signal = strategy.signal(price)
            print(f"Strategy signal {signal}")

            # Check risk limits
            if not risk.can_trade():
                print("RiskManager: Trading blocked")
                time.sleep(TRADE_INTERVAL)
                continue

            # Arbitrage scan
            arb_opps = arb.scan(price)
            if arb_opps:
                print(f"Arbitrage opportunities: {arb_opps}")

            # Liquidity check
            liq_price = liquidity.get_price()
            if liq_price is None:
                print("ENGINE ERROR: DEX price fetch failed")
                time.sleep(TRADE_INTERVAL)
                continue

            # Execute simulated trades
            if signal == "BUY" and portfolio.balance_usd >= price:
                executor.execute_buy(portfolio, price, 1)
            elif signal == "SELL" and portfolio.balance_eth >= 1:
                executor.execute_sell(portfolio, price, 1)

            # Print portfolio summary
            print("Portfolio:", portfolio.summary())

            time.sleep(TRADE_INTERVAL)

    except KeyboardInterrupt:
        print("Trading stopped by user")

if __name__ == "__main__":
    run()
