#!/usr/bin/env python3
"""
aave_flashloan.py — Flash loan via Aave V3 (Sepolia testnet).
Borrows WETH using the flashLoan function and sends profit to your wallet.

IMPORTANT: This script calls Pool.flashLoan().  Aave will then call
executeOperation() on the receiver contract.  You must deploy a contract
that implements IFlashLoanReceiver and repays the loan + 0.05% premium.
Set FLASH_RECEIVER_ADDRESS in .env to your deployed contract address.
"""

import os
from dotenv import load_dotenv
from web3 import Web3
from vault.wallet_config import WalletConfig

load_dotenv(".env")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")

if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set in .env")
if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")

# --- Initialize Web3 ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError("Web3 connection failed — check RPC_URL")

print("Connected to network, chain ID:", w3.eth.chain_id)

# --- Setup executor wallet ---
wallet = WalletConfig(PRIVATE_KEY, RPC_URL)
print("Executor Wallet:", wallet.account.address)

# --- Aave V3 Sepolia addresses (official) ---
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

pool = w3.eth.contract(address=AAVE_POOL_ADDRESS, abi=AAVE_POOL_ABI)
print("Lending Pool connected:", pool.address)

# --- Flash Loan parameters ---
BORROW_AMOUNT_ETH = float(os.getenv("FLASH_LOAN_AMOUNT_ETH", "0.01"))
borrow_amount_wei = w3.to_wei(BORROW_AMOUNT_ETH, "ether")

assets = [WETH_ADDRESS]
amounts = [borrow_amount_wei]
modes = [0]   # 0 = no debt — flash loan only (must repay within same tx)
params = b""
referral_code = 0

print(f"Borrowing {BORROW_AMOUNT_ETH} WETH via flash loan ...")

# --- Build transaction ---
tx = pool.functions.flashLoan(
    FLASH_RECEIVER,   # receiverAddress (IFlashLoanReceiver contract)
    assets,
    amounts,
    modes,
    PROFIT_WALLET,    # onBehalfOf
    params,
    referral_code,
).build_transaction({
    "from": wallet.account.address,
    "nonce": w3.eth.get_transaction_count(wallet.account.address),
    "gas": 500_000,
    "gasPrice": w3.eth.gas_price,
    "chainId": w3.eth.chain_id,
})

# --- Sign and send ---
signed_tx = wallet.account.sign_transaction(tx)
tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print("Flash loan TX sent! TX hash:", tx_hash.hex())
