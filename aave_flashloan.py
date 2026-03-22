#!/usr/bin/env python3
# aave_flashloan.py
# Flash loan example on Sepolia using WETH
# Profit is sent to personal wallet only

from dotenv import load_dotenv
import os
from web3 import Web3
from vault.wallet_config import WalletConfig

# -----------------------------
# Load environment variables
# -----------------------------
load_dotenv(".env")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")

# -----------------------------
# Initialize Web3
# -----------------------------
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise Exception("Web3 connection failed")

print("Connected to Sepolia:", w3.is_connected())

# -----------------------------
# Setup executor wallet
# -----------------------------
wallet = WalletConfig(PRIVATE_KEY, RPC_URL)
print("Executor Wallet:", wallet.account.address)

# -----------------------------
# Aave Lending Pool & WETH addresses (checksummed)
# -----------------------------
LENDING_POOL_ADDRESS = Web3.to_checksum_address("0x2F6B6ACBdF3F7A530F8B32aAe7C420F41C5C9F82")
WETH_ADDRESS = Web3.to_checksum_address("0xA13F3Dc3E6F0BdD942AAdD7f52F5830DAB0f8F84")  # Example Sepolia WETH

# Profit wallet (your wallet)
PROFIT_WALLET = Web3.to_checksum_address(wallet.account.address)

# -----------------------------
# Connect to Lending Pool contract
# -----------------------------
# Note: Replace LENDING_POOL_ABI with actual ABI JSON list
LENDING_POOL_ABI = [
    # Minimal ABI for flash loan function
    {
        "inputs": [
            {"internalType": "address", "name": "receiverAddress", "type": "address"},
            {"internalType": "address[]", "name": "assets", "type": "address[]"},
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"},
            {"internalType": "uint256[]", "name": "modes", "type": "uint256[]"},
            {"internalType": "address", "name": "onBehalfOf", "type": "address"},
            {"internalType": "bytes", "name": "params", "type": "bytes"},
            {"internalType": "uint16", "name": "referralCode", "type": "uint16"}
        ],
        "name": "flashLoan",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

lending_pool = w3.eth.contract(address=LENDING_POOL_ADDRESS, abi=LENDING_POOL_ABI)
print("Lending Pool connected")

# -----------------------------
# Flash Loan parameters
# -----------------------------
BORROW_AMOUNT_ETH = 0.01  # Example borrow amount
borrow_amount_wei = w3.to_wei(BORROW_AMOUNT_ETH, "ether")

assets = [WETH_ADDRESS]
amounts = [borrow_amount_wei]
modes = [0]  # 0 = no debt, flash loan only
receiver_address = PROFIT_WALLET
params = b''  # Optional encoded data
referral_code = 0

# -----------------------------
# Build transaction
# -----------------------------
transaction = lending_pool.functions.flashLoan(
    receiver_address,
    assets,
    amounts,
    modes,
    PROFIT_WALLET,
    params,
    referral_code
).build_transaction({
    "from": wallet.account.address,
    "nonce": w3.eth.get_transaction_count(wallet.account.address),
    "gas": 500000,
    "gasPrice": w3.to_wei("20", "gwei")
})

# -----------------------------
# Sign and send transaction
# -----------------------------
signed_tx = wallet.account.sign_transaction(transaction)
tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print("Flash loan TX sent! TX hash:", tx_hash.hex())
