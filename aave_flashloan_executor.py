#!/usr/bin/env python3
"""
Aave Flash Loan Executor — Safe Test Version
- Borrow WETH on Sepolia testnet
- Profit stored in your wallet
- Dry-run safe by default
"""

from web3 import Web3
from dotenv import load_dotenv
import os

load_dotenv(".env")

# --- Configuration ---
RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
PROFIT_WALLET = "0x64DC101bb50C692b05080595B40D95f93c878A44"  # Your wallet
LENDING_POOL_ADDRESS = "0x2f6b6ACBdF3F7A530f8b32eAe7c420F41C5c9f82"  # Sepolia Aave v3
DRY_RUN = True  # Change to False to actually send TX

# --- Connect Web3 ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))

if not w3.is_connected():
    raise Exception("Web3 connection failed")

account = w3.eth.account.from_key(PRIVATE_KEY)
print("Connected to Sepolia:", w3.is_connected())
print("Executor Wallet:", account.address)
print("Chain ID:", w3.eth.chain_id)

# --- Convert lending pool to checksummed address ---
LENDING_POOL_ADDRESS = w3.to_checksum_address(LENDING_POOL_ADDRESS)

# --- Example Flash Loan Transaction (Simplified) ---
# WARNING: This is a template; real flash loan execution requires smart contract interaction
nonce = w3.eth.get_transaction_count(account.address)
tx = {
    "from": account.address,
    "to": LENDING_POOL_ADDRESS,
    "value": 0,
    "gas": 500_000,
    "gasPrice": w3.to_wei("10", "gwei"),
    "nonce": nonce,
    "chainId": w3.eth.chain_id,
    "data": b"",  # Replace with encoded flash loan call if using a smart contract
}

# --- Sign transaction ---
signed_tx = account.sign_transaction(tx)
print("TX ready. Dry run mode:", DRY_RUN)

# --- Send transaction ---
if not DRY_RUN:
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    print("Transaction broadcasted")
    print("TX HASH:", tx_hash.hex())
else:
    print("Dry run — TX not sent")
    print("Signed TX raw data:", signed_tx.raw_transaction.hex())
