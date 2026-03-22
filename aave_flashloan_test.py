#!/usr/bin/env python3
# aave_flashloan_test.py
# Safe flash loan test script on Sepolia

from web3 import Web3
from dotenv import load_dotenv
import os
from vault.wallet_config import WalletConfig

# Load environment variables
load_dotenv(".env")

# Initialize Web3
RPC_URL = os.getenv("RPC_URL")
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise Exception("Web3 connection failed")

# Load wallet
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
wallet = WalletConfig(PRIVATE_KEY, RPC_URL)

print("Connected to Sepolia:", w3.is_connected())
print("Executor Wallet:", wallet.account.address)

# --- Dummy flash loan variables ---
# Use a checksummed address for lending pool
LENDING_POOL_ADDRESS = Web3.to_checksum_address("0x2f6b6ACBdF3F7A530f8b32eAe7c420F41C5c9f82")  # Example Sepolia pool
LENDING_POOL_ABI = []  # Minimal ABI if needed, else leave empty for safe dry-run

receiver_address = wallet.account.address
assets = []       # Example: ["WETH"] address if needed
amounts = []      # Example: [1e18] for 1 WETH
modes = []        # Loan modes (0 = no debt)
params = b''      # Extra parameters
referral_code = 0
PROFIT_WALLET = wallet.account.address

# Create contract object
try:
    lending_pool = w3.eth.contract(address=LENDING_POOL_ADDRESS, abi=LENDING_POOL_ABI)
    print("Lending Pool connected:", lending_pool.address)
except Exception as e:
    print("Error connecting to Lending Pool (safe for dry-run):", e)

# Build a dummy transaction
try:
    transaction = {
        "from": wallet.account.address,
        "nonce": w3.eth.get_transaction_count(wallet.account.address),
        "gas": 500000,
        "gasPrice": w3.to_wei("20", "gwei"),
        "value": 0
    }

    # Sign transaction
    signed_tx = wallet.account.sign_transaction(transaction)

    print("\n==== SAFE FLASH LOAN TEST ====")
    print("Transaction built successfully!")
    print("TX nonce:", transaction["nonce"])
    print("TX gas:", transaction["gas"])
    print("TX gas price:", transaction["gasPrice"])
    print("TX value:", transaction["value"])
    print("TX not sent — dry run safe ✅")

except Exception as e:
    print("Error building flash loan transaction:", e)
