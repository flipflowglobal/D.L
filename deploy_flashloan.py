from web3 import Web3
from dotenv import load_dotenv
import os

load_dotenv(".env")

w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
private_key = os.getenv("PRIVATE_KEY")
account = w3.eth.account.from_key(private_key)

# Load compiled contract
with open("build/FlashLoanArbitrage_sol_FlashLoanArbitrage.bin") as f:
    bytecode = f.read()
with open("build/FlashLoanArbitrage_sol_FlashLoanArbitrage.abi") as f:
    abi = f.read()

# Pool and Router placeholders (replace with real addresses)
POOL_ADDRESS = "0x0000000000000000000000000000000000000000"
ROUTER_ADDRESS = "0x0000000000000000000000000000000000000000"

contract = w3.eth.contract(abi=abi, bytecode=bytecode)
nonce = w3.eth.get_transaction_count(account.address)

tx = contract.constructor(POOL_ADDRESS, ROUTER_ADDRESS).build_transaction({
    "chainId": w3.eth.chain_id,
    "from": account.address,
    "nonce": nonce,
    "gas": 3000000,
    "gasPrice": w3.to_wei("50", "gwei")
})

signed_tx = w3.eth.account.sign_transaction(tx, private_key)
tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print("Deployment TX hash:", tx_hash.hex())
