#!/usr/bin/env python3
# send_testnet_tx.py

from dotenv import load_dotenv
import os

from vault.wallet_config import WalletConfig
from engine.execution.web3_executor import Web3Executor


def main():

    # Load .env variables
    load_dotenv(".env")

    PRIVATE_KEY = os.getenv("PRIVATE_KEY")
    RPC_URL = os.getenv("RPC_URL")

    # Receiver wallet
    RECIPIENT = "0x49Bd5CacB8402DA3Bf30EC9F21500846B6a8E8aE"

    # Amount to send
    AMOUNT_ETH = 0.01

    print("Loading wallet...")

    wallet = WalletConfig(PRIVATE_KEY, RPC_URL)

    executor = Web3Executor(wallet, RPC_URL)

    print("Wallet address:", wallet.account.address)
    print("Recipient:", RECIPIENT)
    print("Amount:", AMOUNT_ETH, "ETH")

    print("Sending testnet transaction...")

    tx_hash = executor.send_eth(RECIPIENT, AMOUNT_ETH)

    print("Transaction sent!")
    print("TX HASH:", tx_hash)


if __name__ == "__main__":
    main()
