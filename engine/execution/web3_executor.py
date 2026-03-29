from web3 import Web3
from vault.wallet_config import WalletConfig


class Web3Executor:
    """
    Executes real on-chain ETH transfers using a WalletConfig.
    """

    def __init__(self, wallet: WalletConfig, rpc_url: str):
        self.wallet = wallet
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))

        if not self.w3.is_connected():
            raise ConnectionError(f"Web3 connection failed for RPC: {rpc_url}")

    def send_eth(self, to_address: str, amount_eth: float) -> str:
        """Transfer ETH and return the hex transaction hash."""
        nonce = self.w3.eth.get_transaction_count(self.wallet.account.address)
        tx = {
            "nonce": nonce,
            "to": to_address,
            "value": self.w3.to_wei(amount_eth, "ether"),
            "gas": 21000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id,
        }
        signed_tx = self.wallet.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return tx_hash.hex()
