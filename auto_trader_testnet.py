from dotenv import load_dotenv
import os
from vault.wallet_config import WalletConfig
from engine.execution.executor import Executor
from engine.portfolio import Portfolio
from engine.strategies.mean_reversion import MeanReversionStrategy
from time import sleep

# Load environment variables
load_dotenv(".env")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")

# Initialize wallet and executor
wallet = WalletConfig(PRIVATE_KEY, RPC_URL)
executor = Executor(wallet, RPC_URL)

print("Wallet connected:", wallet.account.address)
print("RPC Connected:", executor.web3.is_connected())

# Initialize portfolio and strategy
portfolio = Portfolio()
strategy = MeanReversionStrategy()

# Auto-trading loop (simplified example)
for step in range(5):  # test 5 cycles
    price = 2000  # placeholder for real-time price, integrate MarketData later
    signal = strategy.signal(price)

    print(f"Step {step+1}: Price {price}, Signal {signal}")

    if signal == "BUY":
        if executor.execute_buy(portfolio, price, 1):
            print("Bought 1 ETH")
    elif signal == "SELL":
        if executor.execute_sell(portfolio, price, 1):
            print("Sold 1 ETH")
    else:
        print("Holding position")

    print("Portfolio:", portfolio.balance_usd, "USD,", portfolio.balance_eth, "ETH")
    sleep(2)  # wait 2 seconds between iterations
