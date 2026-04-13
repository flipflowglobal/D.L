#!/usr/bin/env python3
"""
send_test.py — Send a small test ETH transfer using vault/wallet_manager.
Reads all configuration from .env / environment variables.
"""

import os
from dotenv import load_dotenv
from vault.wallet_manager import send_transaction

load_dotenv(".env")

TO_ADDRESS = os.getenv("TEST_RECIPIENT")
AMOUNT_ETH = float(os.getenv("TEST_AMOUNT_ETH", "0.001"))
RPC_URL = os.getenv("RPC_URL")

if not TO_ADDRESS:
    raise RuntimeError(
        "TEST_RECIPIENT not set. Add it to .env:\n"
        "  TEST_RECIPIENT=0xYourRecipientAddress"
    )
if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")

print(f"Sending {AMOUNT_ETH} ETH to {TO_ADDRESS} via {RPC_URL[:40]}...")

tx_hash = send_transaction(
    to_address=TO_ADDRESS,
    amount_eth=AMOUNT_ETH,
    rpc_url=RPC_URL,
)

print("Transaction sent. Hash:", tx_hash)
