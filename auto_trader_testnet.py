#!/usr/bin/env python3
"""
auto_trader_testnet.py — Simple testnet auto-trading loop.
Uses live ETH/USD price from MarketData, simulated execution via Executor.
"""

from dotenv import load_dotenv
import os
from vault.wallet_config import WalletConfig
from engine.execution.executor import Executor
from engine.market_data import MarketData
from engine.portfolio import Portfolio
from engine.strategies.mean_reversion import MeanReversionStrategy
from time import sleep

load_dotenv(".env")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")

if not PRIVATE_KEY or not RPC_URL:
    raise RuntimeError("PRIVATE_KEY and RPC_URL must be set in .env")

# Initialize wallet and executor
wallet = WalletConfig(PRIVATE_KEY, RPC_URL)
executor = Executor(wallet, RPC_URL)

print("Wallet connected:", wallet.account.address)
print("RPC Connected   :", wallet.is_connected())

# Initialize market data, portfolio and strategy
market = MarketData()
portfolio = Portfolio()
strategy = MeanReversionStrategy()

TRADE_CYCLES = int(os.getenv("TRADE_CYCLES", "5"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "2"))

print(f"\nRunning {TRADE_CYCLES} trade cycles (interval {SCAN_INTERVAL}s) ...\n")

for step in range(TRADE_CYCLES):
    price = market.get_price()
    signal = strategy.signal(price)

    print(f"Step {step + 1}/{TRADE_CYCLES}: ETH ${price:,.2f}  Signal: {signal}")

    if signal == "BUY":
        if executor.execute_buy(portfolio, price, 1):
            print("  -> Bought 1 ETH")
    elif signal == "SELL":
        if executor.execute_sell(portfolio, price, 1):
            print("  -> Sold 1 ETH")
    else:
        print("  -> Holding")

    print(f"  Portfolio: ${portfolio.balance_usd:,.2f} USD  |  {portfolio.balance_eth:.4f} ETH")

    if step < TRADE_CYCLES - 1:
        sleep(SCAN_INTERVAL)

print("\nTrading session complete.")
print("Final portfolio:", portfolio.summary())
