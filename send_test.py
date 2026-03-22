# ~/OnTheDL/send_test.py
from vault.wallet_manager import send_transaction

# Replace with your recipient and RPC endpoint
TO_ADDRESS = "0xRecipientAddressHere"  # example
AMOUNT_ETH = 0.001                     # small test amount
RPC_URL = "https://mainnet.infura.io/v3/YOUR_INFURA_PROJECT_ID"  # Replace with your Infura/Alchemy RPC

tx_hash = send_transaction(
    to_address=TO_ADDRESS,
    amount_eth=AMOUNT_ETH,
    rpc_url=RPC_URL
)

print("Transaction sent. Hash:", tx_hash)
