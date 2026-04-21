"""
Testnet Wallet Manager
──────────────────────
Reads wallet credentials from environment variables or vault/wallet.json.
Run `python setup_wallet.py` first to initialise the vault.
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

WALLET_FILE = os.path.join(os.path.dirname(__file__), "vault", "wallet.json")


def load_wallet() -> dict:
    """Load wallet from vault file, falling back to environment variables."""
    if os.path.exists(WALLET_FILE):
        with open(WALLET_FILE, encoding="utf-8") as f:
            return json.load(f)

    address = os.getenv("WALLET_ADDRESS")
    private_key = os.getenv("PRIVATE_KEY")

    if not address or not private_key:
        raise RuntimeError(
            "No wallet found. Run `python setup_wallet.py` to create one, "
            "or set WALLET_ADDRESS and PRIVATE_KEY in .env"
        )
    return {"address": address, "private_key": private_key}


def send_transaction(to_address: str, amount_eth: float, rpc_url: str) -> str:
    """Sign and broadcast an ETH transfer. Returns the hex tx hash."""
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to RPC: {rpc_url}")

    wallet = load_wallet()
    nonce = w3.eth.get_transaction_count(wallet["address"])
    tx = {
        "type":                 "0x2",
        "nonce":                nonce,
        "to":                   Web3.to_checksum_address(to_address),
        "value":                w3.to_wei(amount_eth, "ether"),
        "gas":                  21_000,
        "maxFeePerGas":         w3.to_wei(100, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(2,   "gwei"),
        "chainId":              w3.eth.chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, wallet["private_key"])
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()


if __name__ == "__main__":
    wallet = load_wallet()
    print("Wallet address:", wallet["address"])

    rpc_url = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
    if not rpc_url:
        print("Set RPC_URL in .env to send a test transaction.")
    else:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        balance = w3.eth.get_balance(wallet["address"])
        print(f"Balance: {w3.from_wei(balance, 'ether'):.6f} ETH")
