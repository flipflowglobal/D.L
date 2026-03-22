# ~/OnTheDL/testnet_wallet_manager.py
from web3 import Web3
import json
import os

# ---------------------------
# TEST WALLET (safe, testnet)
# ---------------------------
PRIVATE_KEY = "07b5834f1ba400134671c6b301be3c71472ae801593a26154f787704fb7c17e9"
WALLET_ADDRESS = "0x7FD7f50ed0D0625887072a95426F6A5d1e0BD3bF"

# Vault file
WALLET_FILE = "vault/wallet.json"

# ---------------------------
# Initialize Vault
# ---------------------------
def initialize_wallet():
    os.makedirs("vault", exist_ok=True)
    if not os.path.exists(WALLET_FILE):
        data = {
            "address": WALLET_ADDRESS,
            "private_key": PRIVATE_KEY
        }
        with open(WALLET_FILE, "w") as f:
            json.dump(data, f)
        print(f"Test wallet saved: {WALLET_ADDRESS}")
    else:
        print("Wallet file already exists.")

# ---------------------------
# Load Wallet
# ---------------------------
def load_wallet():
    if not os.path.exists(WALLET_FILE):
        initialize_wallet()
    with open(WALLET_FILE, "r") as f:
        return json.load(f)

# ---------------------------
# Send Transaction
# ---------------------------
def send_transaction(to_address, amount_eth, rpc_url):
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    wallet = load_wallet()
    from_addr = wallet["address"]
    private_key = wallet["private_key"]

    nonce = w3.eth.get_transaction_count(from_addr)
    tx = {
        "nonce": nonce,
        "to": to_address,
        "value": w3.toWei(amount_eth, "ether"),
        "gas": 21000,
        "gasPrice": w3.eth.gas_price,
    }
    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
    return w3.toHex(tx_hash)

# ---------------------------
# Main Test
# ---------------------------
if __name__ == "__main__":
    initialize_wallet()
    wallet = load_wallet()
    print("Wallet address:", wallet["address"])

    # Safe test transaction example (Goerli or Sepolia testnet)
    RPC_URL = "https://goerli.infura.io/v3/YOUR_INFURA_PROJECT_ID"  # Replace with your testnet RPC
    TO_ADDRESS = "0x000000000000000000000000000000000000dEaD"  # burn address, safe for testing
    AMOUNT_ETH = 0.00001

    tx_hash = send_transaction(TO_ADDRESS, AMOUNT_ETH, RPC_URL)
    print("Test transaction sent. Hash:", tx_hash)
