#!/usr/bin/env python3
"""
check_balance.py — Display ETH balance of the configured wallet.

Reads PRIVATE_KEY and RPC_URL from .env and prints the address,
chain ID, and current ETH balance.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(".env")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL     = os.getenv("RPC_URL")

if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set in .env — run setup_wallet.py first")
if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")

from vault.wallet_config import WalletConfig

wallet = WalletConfig(PRIVATE_KEY, RPC_URL)

if not wallet.is_connected():
    raise RuntimeError(f"Web3 connection failed — check RPC_URL: {RPC_URL}")

balance_eth = wallet.get_balance_eth()
chain_id    = wallet.w3.eth.chain_id

print(f"Wallet address : {wallet.address}")
print(f"Chain ID       : {chain_id}")
print(f"Balance (ETH)  : {balance_eth:.6f}")
