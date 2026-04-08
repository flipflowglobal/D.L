from web3 import Web3


class WalletConfig:
    """
    Wraps a private key + RPC endpoint into a ready-to-use wallet object.
    Exposes `account` (LocalAccount) with address and sign_transaction().
    """

    def __init__(self, private_key: str, rpc_url: str):
        if not private_key:
            raise ValueError("PRIVATE_KEY is not set")
        if not rpc_url:
            raise ValueError("RPC_URL is not set")

        # Strip 0x prefix if present — web3 accepts either form
        key = private_key.strip()
        if key.lower().startswith("0x"):
            key = key[2:]

        self.private_key = key
        self.rpc_url = rpc_url
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.account = self.w3.eth.account.from_key(key)

    @property
    def address(self) -> str:
        return self.account.address

    def is_connected(self) -> bool:
        return self.w3.is_connected()
