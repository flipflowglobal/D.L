#!/usr/bin/env python3
"""
deploy_flashloan.py — Deploy the FlashLoanArbitrage contract to Sepolia.
Requires compiled artifacts in build/:
  build/FlashLoanArbitrage_sol_FlashLoanArbitrage.bin
  build/FlashLoanArbitrage_sol_FlashLoanArbitrage.abi

Set AAVE_POOL_ADDRESS and UNISWAP_ROUTER_ADDRESS in .env, or the
Aave V3 Sepolia defaults will be used.
"""

import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv(".env")

RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set in .env")

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError("Web3 connection failed — check RPC_URL")

account = w3.eth.account.from_key(PRIVATE_KEY)
print("Deployer wallet:", account.address)
print("Chain ID       :", w3.eth.chain_id)

# --- Load compiled contract ---
BIN_PATH = "build/FlashLoanArbitrage_sol_FlashLoanArbitrage.bin"
ABI_PATH = "build/FlashLoanArbitrage_sol_FlashLoanArbitrage.abi"

if not os.path.exists(BIN_PATH) or not os.path.exists(ABI_PATH):
    raise FileNotFoundError(
        "Compiled contract artifacts not found.\n"
        "Run: solc --bin --abi contracts/FlashLoanArbitrage.sol -o build/"
    )

with open(BIN_PATH, encoding="utf-8") as f:
    bytecode = f.read().strip()
with open(ABI_PATH, encoding="utf-8") as f:
    abi = f.read().strip()

# --- Aave V3 Sepolia Pool and Uniswap V3 SwapRouter (official addresses) ---
POOL_ADDRESS = Web3.to_checksum_address(
    os.getenv("AAVE_POOL_ADDRESS", "0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951")
)
ROUTER_ADDRESS = Web3.to_checksum_address(
    os.getenv("UNISWAP_ROUTER_ADDRESS", "0xE592427A0AEce92De3Edee1F18E0157C05861564")
)

print("Aave Pool   :", POOL_ADDRESS)
print("Uni Router  :", ROUTER_ADDRESS)

contract = w3.eth.contract(abi=abi, bytecode=bytecode)
nonce = w3.eth.get_transaction_count(account.address)

tx = contract.constructor(POOL_ADDRESS, ROUTER_ADDRESS).build_transaction({
    "chainId":              w3.eth.chain_id,
    "from":                 account.address,
    "nonce":                nonce,
    "gas":                  3_000_000,
    "maxFeePerGas":         w3.to_wei(100, "gwei"),
    "maxPriorityFeePerGas": w3.to_wei(2,   "gwei"),
})

signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print("\nDeployment TX sent!")
print("TX HASH:", tx_hash.hex())

# Wait for receipt
print("Waiting for confirmation ...")
receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
print("Contract deployed at:", receipt.contractAddress)
