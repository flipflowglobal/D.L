#!/usr/bin/env python3
# check_balance.py
# Checks ETH balance of your wallet on Sepolia testnet

from dotenv import load_dotenv
import os
from vault.wallet_config import WalletConfig
from web3 import Web3

# Load environment variables
load_dotenv(".env")

# Initialize wallet
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")

if not PRIVATE_KEY or not RPC_URL:
    raise Exception("PRIVATE_KEY or RPC_URL not set in .env")

wallet = WalletConfig(PRIVATE_KEY, RPC_URL)
w3 = Web3(Web3.HTTPProvider(RPC_URL))

if not w3.is_connected():
    raise Exception("Web3 connection failed. Check RPC_URL.")

# Fetch and display balance
balance_wei = w3.eth.get_balance(wallet.account.address)
balance_eth = w3.from_wei(balance_wei, "ether")

print("Wallet address:", wallet.account.address)
print("Balance (ETH):", balance_eth)
print("Chain ID:", w3.eth.chain_id)
