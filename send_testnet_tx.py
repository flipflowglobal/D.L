#!/usr/bin/env python3
"""
send_testnet_tx.py — Send a small test ETH transfer via Web3Executor.

Reads PRIVATE_KEY, RPC_URL, TEST_RECIPIENT, and TEST_AMOUNT_ETH from .env.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from vault.wallet_config import WalletConfig
from engine.execution.web3_executor import Web3Executor


def main():

    # Load .env variables
    load_dotenv(".env")

    PRIVATE_KEY = os.getenv("PRIVATE_KEY")
    RPC_URL = os.getenv("RPC_URL")

    # Receiver wallet — set TEST_RECIPIENT in .env
    RECIPIENT = os.getenv("TEST_RECIPIENT")
    if not RECIPIENT:
        raise RuntimeError(
            "TEST_RECIPIENT not set. Add it to .env:\n"
            "  TEST_RECIPIENT=0xYourRecipientAddress"
        )

    # Amount to send (configurable via TEST_AMOUNT_ETH in .env)
    AMOUNT_ETH = float(os.getenv("TEST_AMOUNT_ETH", "0.01"))

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
