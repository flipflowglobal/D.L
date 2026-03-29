import json
import os

from web3 import Web3

WALLET_FILE = os.path.join(os.path.dirname(__file__), "wallet.json")


def load_wallet() -> dict:
    if not os.path.exists(WALLET_FILE):
        raise FileNotFoundError(
            f"Wallet file not found: {WALLET_FILE}. "
            "Run testnet_wallet_manager.py to initialise it."
        )
    with open(WALLET_FILE) as f:
        return json.load(f)


def send_transaction(to_address: str, amount_eth: float, rpc_url: str) -> str:
    """Sign and broadcast an ETH transfer. Returns the hex tx hash."""
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"Web3 connection failed for RPC: {rpc_url}")

    wallet = load_wallet()
    from_addr = wallet["address"]
    private_key = wallet["private_key"]

    nonce = w3.eth.get_transaction_count(from_addr)
    tx = {
        "nonce": nonce,
        "to": to_address,
        "value": w3.to_wei(amount_eth, "ether"),
        "gas": 21000,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    }
    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    return tx_hash.hex()
