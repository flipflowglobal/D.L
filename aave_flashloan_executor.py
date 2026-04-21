#!/usr/bin/env python3
"""
Aave V3 Flash Loan Executor — Sepolia Testnet
Borrows WETH via flashLoanSimple, dry-run by default.
Set DRY_RUN=false in .env to broadcast the transaction.

NOTE: For a real flash loan you must deploy a receiver contract that
implements IFlashLoanSimpleReceiver and repays the loan + premium
(0.05 %) inside executeOperation().  This script encodes the call
and signs it; in dry-run mode it only prints the signed bytes.
"""

import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv(".env")

# --- Configuration ---
RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
# PROFIT_WALLET defaults to the executor's own address if not set explicitly
PROFIT_WALLET = os.getenv("PROFIT_WALLET")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() not in ("false", "0", "no")

# --- Aave V3 Sepolia addresses (official) ---
AAVE_POOL_ADDRESS = Web3.to_checksum_address(
    os.getenv("AAVE_POOL_ADDRESS", "0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951")
)
WETH_ADDRESS = Web3.to_checksum_address(
    os.getenv("WETH_ADDRESS", "0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c")
)

# --- Aave V3 Pool ABI (minimal — flashLoanSimple only) ---
AAVE_POOL_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "receiverAddress", "type": "address"},
            {"internalType": "address", "name": "asset", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "bytes", "name": "params", "type": "bytes"},
            {"internalType": "uint16", "name": "referralCode", "type": "uint16"},
        ],
        "name": "flashLoanSimple",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set in .env")

# --- Connect Web3 ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError("Web3 connection failed — check RPC_URL")

account = w3.eth.account.from_key(PRIVATE_KEY)
profit_wallet = Web3.to_checksum_address(PROFIT_WALLET or account.address)

print("Connected to network, chain ID:", w3.eth.chain_id)
print("Executor wallet :", account.address)
print("Profit wallet   :", profit_wallet)
print("Dry run         :", DRY_RUN)

# --- Borrow parameters ---
BORROW_AMOUNT = w3.to_wei(float(os.getenv("FLASH_LOAN_AMOUNT_ETH", "0.01")), "ether")

pool = w3.eth.contract(address=AAVE_POOL_ADDRESS, abi=AAVE_POOL_ABI)

# --- Encode flash loan call ---
# receiverAddress = profit_wallet (must implement IFlashLoanSimpleReceiver on-chain)
tx = pool.functions.flashLoanSimple(
    profit_wallet,   # receiverAddress
    WETH_ADDRESS,    # asset
    BORROW_AMOUNT,   # amount
    b"",             # params (passed through to executeOperation)
    0,               # referralCode
).build_transaction({
    "from":                 account.address,
    "nonce":                w3.eth.get_transaction_count(account.address),
    "gas":                  500_000,
    "maxFeePerGas":         w3.to_wei(100, "gwei"),
    "maxPriorityFeePerGas": w3.to_wei(2,   "gwei"),
    "chainId":              w3.eth.chain_id,
})

signed_tx = account.sign_transaction(tx)
print(f"\nFlash loan TX encoded ({len(signed_tx.raw_transaction)} bytes)")
print("TX nonce      :", tx["nonce"])
print("Gas price     :", w3.from_wei(tx["gasPrice"], "gwei"), "gwei")

if not DRY_RUN:
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    print("\nTransaction broadcasted!")
    print("TX HASH:", tx_hash.hex())
else:
    print("\nDry run — TX not sent")
    print("Signed TX (hex):", signed_tx.raw_transaction.hex()[:80], "...")
