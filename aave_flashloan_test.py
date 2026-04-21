#!/usr/bin/env python3
"""
aave_flashloan_test.py — Dry-run flash loan test on Sepolia.
Builds and signs the flashLoan transaction without broadcasting it.
Verifies ABI encoding and wallet connectivity.
"""

import os
from web3 import Web3
from dotenv import load_dotenv
from vault.wallet_config import WalletConfig

load_dotenv(".env")

RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set in .env")

# Initialize Web3
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError("Web3 connection failed — check RPC_URL")

wallet = WalletConfig(PRIVATE_KEY, RPC_URL)
print("Connected to network, chain ID:", w3.eth.chain_id)
print("Executor Wallet:", wallet.account.address)

# --- Aave V3 Sepolia (official) ---
AAVE_POOL_ADDRESS = Web3.to_checksum_address(
    os.getenv("AAVE_POOL_ADDRESS", "0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951")
)
WETH_ADDRESS = Web3.to_checksum_address(
    os.getenv("WETH_ADDRESS", "0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c")
)

PROFIT_WALLET = Web3.to_checksum_address(
    os.getenv("PROFIT_WALLET", wallet.account.address)
)
FLASH_RECEIVER = Web3.to_checksum_address(
    os.getenv("FLASH_RECEIVER_ADDRESS", wallet.account.address)
)

BORROW_AMOUNT = w3.to_wei(float(os.getenv("FLASH_LOAN_AMOUNT_ETH", "0.01")), "ether")

# --- Aave V3 Pool ABI ---
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

pool = w3.eth.contract(address=AAVE_POOL_ADDRESS, abi=AAVE_POOL_ABI)
print("Lending Pool contract:", pool.address)

# --- Build and sign (dry-run — no broadcast) ---
try:
    tx = pool.functions.flashLoan(
        FLASH_RECEIVER,
        [WETH_ADDRESS],
        [BORROW_AMOUNT],
        [0],            # no-debt mode
        PROFIT_WALLET,
        b"",
        0,
    ).build_transaction({
        "from":                 wallet.account.address,
        "nonce":                w3.eth.get_transaction_count(wallet.account.address),
        "gas":                  500_000,
        "maxFeePerGas":         w3.to_wei(50, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(2,  "gwei"),
        "chainId":              w3.eth.chain_id,
    })

    signed_tx = wallet.account.sign_transaction(tx)

    print("\n==== DRY-RUN FLASH LOAN TEST ====")
    print("Transaction built and signed successfully!")
    print("TX nonce       :", tx["nonce"])
    print("TX gas         :", tx["gas"])
    print("TX maxFeePerGas:", w3.from_wei(tx["maxFeePerGas"], "gwei"), "gwei")
    print("TX call data   :", tx["data"][:66], "...")
    print("Signed TX bytes:", len(signed_tx.raw_transaction))
    print("TX NOT sent — dry run safe ✅")

except Exception as e:
    print("Error building flash loan transaction:", e)
