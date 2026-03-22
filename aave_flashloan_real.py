#!/usr/bin/env python3
# aave_flashloan_real.py
# Execute a flash loan on Aave v3 on Sepolia
# Borrows WETH and sends profit to your wallet

from web3 import Web3
from dotenv import load_dotenv
import os
from vault.wallet_config import WalletConfig

# Load environment variables
load_dotenv(".env")

# --- Configuration ---
RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
PROFIT_WALLET = "0x64DC101bb50C692b05080595B40D95f93c878A44"  # Your wallet
AMOUNT_WETH = Web3.to_wei(0.01, "ether")  # Amount to borrow

# Aave v3 Sepolia addresses
LENDING_POOL_ADDRESS = Web3.to_checksum_address("0x2f6b6ACBdF3F7A530f8b32eAe7c420F41C5c9f82")  # example: replace with correct
WETH_ADDRESS = Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")  # testnet WETH

# --- Initialize Web3 and wallet ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise Exception("Web3 connection failed")

wallet = WalletConfig(PRIVATE_KEY, RPC_URL)
print("Connected to Sepolia:", w3.is_connected())
print("Executor Wallet:", wallet.account.address)

# --- Contract setup ---
lending_pool_abi = []  # Load Aave LendingPool ABI here (from JSON)
lending_pool = w3.eth.contract(address=LENDING_POOL_ADDRESS, abi=lending_pool_abi)

# --- Prepare transaction ---
nonce = w3.eth.get_transaction_count(wallet.account.address)
gas_price = w3.eth.gas_price

tx = {
    "from": wallet.account.address,
    "to": LENDING_POOL_ADDRESS,
    "value": 0,
    "nonce": nonce,
    "gasPrice": gas_price,
    "gas": 500000,  # Estimate
    "data": b"",    # Add flash loan data here (ABI-encoded)
}

# --- Sign transaction ---
signed_tx = wallet.account.sign_transaction(tx)

# --- Send transaction ---
tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print("Transaction broadcasted")
print("TX HASH:", tx_hash.hex())
