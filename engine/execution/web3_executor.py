"""
engine/execution/web3_executor.py — EIP-1559 ETH transfer executor.

Used for plain ETH sends (e.g. profit withdrawal, test transfers).
For token swaps, use SwapExecutor.
"""

from __future__ import annotations

import logging

from web3 import Web3

from vault.wallet_config import WalletConfig

logger = logging.getLogger("aureon.web3_executor")


class Web3Executor:
    """
    Executes real on-chain EIP-1559 ETH transfers using a WalletConfig.

    Args:
        wallet:  WalletConfig with private key + address.
        rpc_url: JSON-RPC endpoint URL.

    Raises:
        ConnectionError: RPC endpoint is unreachable at construction time.
    """

    def __init__(self, wallet: WalletConfig, rpc_url: str) -> None:
        self.wallet = wallet
        self.w3     = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        if not self.w3.is_connected():
            raise ConnectionError(f"Web3 connection failed for RPC: {rpc_url}")

    def send_eth(
        self,
        to_address: str,
        amount_eth: float,
        *,
        max_fee_gwei: float = 100.0,
        priority_fee_gwei: float = 2.0,
    ) -> str:
        """
        Sign and broadcast an EIP-1559 ETH transfer.

        Args:
            to_address:        recipient checksummed address.
            amount_eth:        ETH to send (not wei).
            max_fee_gwei:      EIP-1559 maxFeePerGas ceiling (Gwei).
            priority_fee_gwei: EIP-1559 miner tip (Gwei).

        Returns:
            Hex transaction hash (0x-prefixed).
        """
        from_addr = self.wallet.account.address
        to_addr   = Web3.to_checksum_address(to_address)
        value_wei = self.w3.to_wei(amount_eth, "ether")
        nonce     = self.w3.eth.get_transaction_count(from_addr)

        tx = {
            "type":                 "0x2",
            "nonce":                nonce,
            "to":                   to_addr,
            "value":                value_wei,
            "gas":                  21_000,
            "maxFeePerGas":         self.w3.to_wei(max_fee_gwei,      "gwei"),
            "maxPriorityFeePerGas": self.w3.to_wei(priority_fee_gwei, "gwei"),
            "chainId":              self.w3.eth.chain_id,
        }
        signed   = self.wallet.account.sign_transaction(tx)
        tx_hash  = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info("ETH sent %.6f ETH → %s  tx=%s", amount_eth, to_addr, tx_hash.hex())
        return tx_hash.hex()
