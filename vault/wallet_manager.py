"""
vault/wallet_manager.py — Wallet file I/O and transaction broadcast.

Provides:
    load_wallet()       — load wallet dict from vault/wallet.json
    send_transaction()  — sign and broadcast an EIP-1559 ETH transfer
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from web3 import Web3

logger = logging.getLogger("aureon.wallet_manager")

WALLET_FILE = Path(__file__).parent / "wallet.json"


def load_wallet() -> dict:
    """
    Load wallet metadata from vault/wallet.json.

    Returns:
        dict with at least ``address`` and ``private_key`` keys.

    Raises:
        FileNotFoundError: vault/wallet.json does not exist.
        ValueError:        file is not valid JSON or missing required keys.
    """
    if not WALLET_FILE.exists():
        raise FileNotFoundError(
            f"Wallet file not found: {WALLET_FILE}. "
            "Run `python setup_wallet.py` or `python generate_wallet.py` to create it."
        )
    with open(WALLET_FILE, encoding="utf-8") as f:
        wallet = json.load(f)

    for key in ("address", "private_key"):
        if key not in wallet:
            raise ValueError(f"wallet.json is missing required key: '{key}'")

    return wallet


def send_transaction(
    to_address: str,
    amount_eth: float,
    rpc_url: str,
    *,
    wallet: Optional[dict] = None,
    max_fee_gwei: float = 100.0,
    priority_fee_gwei: float = 2.0,
) -> str:
    """
    Sign and broadcast an EIP-1559 ETH transfer.

    Args:
        to_address:        recipient checksummed address.
        amount_eth:        ETH amount to send (not wei).
        rpc_url:           JSON-RPC endpoint URL.
        wallet:            wallet dict (loads from file if None).
        max_fee_gwei:      EIP-1559 maxFeePerGas ceiling in Gwei.
        priority_fee_gwei: EIP-1559 miner tip in Gwei.

    Returns:
        Hex transaction hash string (0x-prefixed).

    Raises:
        ConnectionError: RPC endpoint unreachable.
        ValueError:      insufficient funds or invalid address.
    """
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"Web3 connection failed for RPC: {rpc_url}")

    if wallet is None:
        wallet = load_wallet()

    from_addr   = Web3.to_checksum_address(wallet["address"])
    private_key = wallet["private_key"].strip()
    if private_key.lower().startswith("0x"):
        private_key = private_key[2:]

    to_addr     = Web3.to_checksum_address(to_address)
    value_wei   = w3.to_wei(amount_eth, "ether")
    nonce       = w3.eth.get_transaction_count(from_addr)
    chain_id    = w3.eth.chain_id

    tx = {
        "type":                 "0x2",          # EIP-1559
        "nonce":                nonce,
        "to":                   to_addr,
        "value":                value_wei,
        "gas":                  21_000,
        "maxFeePerGas":         w3.to_wei(max_fee_gwei,      "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(priority_fee_gwei, "gwei"),
        "chainId":              chain_id,
    }

    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash   = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    logger.info("TX sent: %s  to=%s  amount=%.6f ETH", tx_hash.hex(), to_addr, amount_eth)
    return tx_hash.hex()
