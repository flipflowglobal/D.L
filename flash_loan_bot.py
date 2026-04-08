#!/usr/bin/env python3
"""
flash_loan_bot.py — Flash loan bot using Aave V3 on Sepolia.
Encodes a flashLoanSimple call and broadcasts it.

IMPORTANT: The receiver address must be a deployed contract implementing
IFlashLoanSimpleReceiver.  Set FLASH_RECEIVER_ADDRESS in .env.
Without a receiver contract this TX will revert on-chain.
"""

import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv(".env")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")

if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set in .env")
if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")

# Web3 connection
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError("Web3 connection failed — check RPC_URL")

account = w3.eth.account.from_key(PRIVATE_KEY)
print("Using wallet:", account.address)
print("Chain ID    :", w3.eth.chain_id)

# --- Aave V3 Sepolia (official) ---
AAVE_POOL_ADDRESS = Web3.to_checksum_address(
    os.getenv("AAVE_POOL_ADDRESS", "0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951")
)
WETH_ADDRESS = Web3.to_checksum_address(
    os.getenv("WETH_ADDRESS", "0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c")
)

# --- Aave V3 Pool ABI (flashLoanSimple) ---
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

pool = w3.eth.contract(address=AAVE_POOL_ADDRESS, abi=AAVE_POOL_ABI)


def execute_flash_loan(amount_wei: int, token_address: str, profit_wallet: str):
    """
    Initiate a flashLoanSimple from Aave V3.
    The receiver contract must:
      1. Receive `amount_wei` of `token_address`
      2. Execute arbitrage / trade logic
      3. Approve and repay amount_wei + premium (0.05%) to the pool
    """
    receiver = Web3.to_checksum_address(
        os.getenv("FLASH_RECEIVER_ADDRESS", profit_wallet)
    )

    print(f"Initiating flash loan: {w3.from_wei(amount_wei, 'ether')} WETH")
    print(f"Receiver contract  : {receiver}")
    print(f"Profit wallet      : {profit_wallet}")

    tx = pool.functions.flashLoanSimple(
        receiver,
        Web3.to_checksum_address(token_address),
        amount_wei,
        b"",   # params passed to executeOperation
        0,     # referralCode
    ).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 500_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    print("Flash loan TX sent! TX HASH:", tx_hash.hex())
    return tx_hash.hex()


if __name__ == "__main__":
    FLASH_LOAN_AMOUNT = w3.to_wei(
        float(os.getenv("FLASH_LOAN_AMOUNT_ETH", "0.01")), "ether"
    )
    PROFIT_WALLET = os.getenv("PROFIT_WALLET", account.address)

    execute_flash_loan(FLASH_LOAN_AMOUNT, WETH_ADDRESS, PROFIT_WALLET)
