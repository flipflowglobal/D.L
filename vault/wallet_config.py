"""
vault/wallet_config.py — Wallet + Web3 connection wrapper.

WalletConfig wraps a raw private key and RPC URL into a signing-ready
object, exposing the LocalAccount (address, sign_transaction) and a live
Web3 connection.
"""

from __future__ import annotations

import logging
from typing import Optional

from web3 import Web3

logger = logging.getLogger("aureon.wallet")


class WalletConfig:
    """
    Wraps a private key + RPC endpoint into a ready-to-use wallet object.

    Attributes:
        address:     checksummed Ethereum address (str).
        account:     web3 LocalAccount — has .sign_transaction().
        w3:          live Web3 instance connected to *rpc_url*.
        private_key: sanitised private key (no 0x prefix).
    """

    def __init__(self, private_key: str, rpc_url: str) -> None:
        if not private_key or not isinstance(private_key, str):
            raise ValueError("PRIVATE_KEY must be a non-empty string")
        if not rpc_url or not isinstance(rpc_url, str):
            raise ValueError("RPC_URL must be a non-empty string")

        # Strip leading 0x — web3 accepts either form but we normalise
        key = private_key.strip()
        if key.lower().startswith("0x"):
            key = key[2:]

        if len(key) != 64:
            raise ValueError(
                f"PRIVATE_KEY must be 32 bytes (64 hex chars), got {len(key)} chars"
            )

        self.private_key = key
        self.rpc_url     = rpc_url
        self.w3          = Web3(Web3.HTTPProvider(rpc_url))
        self.account     = self.w3.eth.account.from_key(key)
        logger.info("Wallet loaded: %s", self.account.address)

    @property
    def address(self) -> str:
        """Checksummed Ethereum address."""
        return self.account.address

    def is_connected(self) -> bool:
        """Return True if the Web3 provider is reachable."""
        return self.w3.is_connected()

    def get_balance_eth(self) -> Optional[float]:
        """Return current ETH balance, or None on RPC failure."""
        try:
            wei = self.w3.eth.get_balance(self.account.address)
            return float(self.w3.from_wei(wei, "ether"))
        except Exception as exc:
            logger.error("get_balance_eth failed: %s", exc)
            return None
