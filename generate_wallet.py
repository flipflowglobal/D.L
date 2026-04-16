#!/usr/bin/env python3
"""
generate_wallet.py — Quick Ethereum wallet generator.

Generates a new private key + address and prints them to stdout.
For full setup (save to vault/wallet.json and patch .env), run:

    python setup_wallet.py
"""

from __future__ import annotations

from eth_account import Account

acct = Account.create()

print("==== NEW WALLET GENERATED ====")
print(f"Address    : {acct.address}")
print(f"Private Key: {acct.key.hex()}")
print()
print("WARNING: Copy and securely store your private key NOW.")
print("Run 'python setup_wallet.py' to save it to vault/wallet.json")
