#!/usr/bin/env python3
"""
aave_flashloan_real.py — Execute a flash loan on Aave V3 (Sepolia testnet).
Borrows WETH and encodes the full flashLoan call via ABI.

IMPORTANT: The receiverAddress must be a deployed contract implementing
IFlashLoanReceiver.  Set FLASH_RECEIVER_ADDRESS in .env to your contract.
If not set, the executor wallet is used as receiver (for call encoding only).
"""

import os
from web3 import Web3
from dotenv import load_dotenv
from vault.wallet_config import WalletConfig

load_dotenv(".env")

# --- Configuration ---
RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
PROFIT_WALLET = os.getenv("PROFIT_WALLET")

# --- Aave V3 Sepolia addresses (official) ---
AAVE_POOL_ADDRESS = Web3.to_checksum_address(
    os.getenv("AAVE_POOL_ADDRESS", "0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951")
)
WETH_ADDRESS = Web3.to_checksum_address(
    os.getenv("WETH_ADDRESS", "0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c")
)

# Borrow amount (default 0.01 WETH)
AMOUNT_WETH = Web3.to_wei(float(os.getenv("FLASH_LOAN_AMOUNT_ETH", "0.01")), "ether")

# --- Aave V3 Pool ABI (flashLoan) ---
AAVE_POOL_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "receiverAddress", "type": "address"},
            {"internalType": "address[]", "name": "assets", "type": "address[]"},
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"},
            {"internalType": "uint256[]", "name": "interestRateModes", "type": "uint256[]"},
            {"internalType": "address", "name": "onBehalfOf", "type": "address"},
            {"internalType": "bytes", "name": "params", "type": "bytes"},
            {"internalType": "uint16", "name": "referralCode", "type": "uint16"},
        ],
        "name": "flashLoan",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set in .env")

# --- Initialize Web3 and wallet ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError("Web3 connection failed — check RPC_URL")

wallet = WalletConfig(PRIVATE_KEY, RPC_URL)
profit_wallet = Web3.to_checksum_address(PROFIT_WALLET or wallet.account.address)

# Receiver: deployed IFlashLoanReceiver contract, or wallet for dry testing
receiver_address = Web3.to_checksum_address(
    os.getenv("FLASH_RECEIVER_ADDRESS", wallet.account.address)
)

print("Connected to network, chain ID:", w3.eth.chain_id)
print("Executor wallet  :", wallet.account.address)
print("Receiver contract:", receiver_address)
print("Profit wallet    :", profit_wallet)

# --- Contract setup ---
pool = w3.eth.contract(address=AAVE_POOL_ADDRESS, abi=AAVE_POOL_ABI)

# --- Build flash loan transaction ---
tx = pool.functions.flashLoan(
    receiver_address,       # receiverAddress
    [WETH_ADDRESS],         # assets
    [AMOUNT_WETH],          # amounts
    [0],                    # interestRateModes (0 = no debt / flash loan)
    profit_wallet,          # onBehalfOf
    b"",                    # params
    0,                      # referralCode
).build_transaction({
    "from":                 wallet.account.address,
    "nonce":                w3.eth.get_transaction_count(wallet.account.address),
    "gas":                  500_000,
    "maxFeePerGas":         w3.to_wei(100, "gwei"),
    "maxPriorityFeePerGas": w3.to_wei(2,   "gwei"),
    "chainId":              w3.eth.chain_id,
})

# --- Sign transaction ---
signed_tx = wallet.account.sign_transaction(tx)
print(f"\nFlash loan TX encoded ({len(signed_tx.raw_transaction)} bytes)")
print("TX nonce      :", tx["nonce"])

# --- Send transaction ---
tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print("\nTransaction broadcasted!")
print("TX HASH:", tx_hash.hex())
