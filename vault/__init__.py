"""
vault — Wallet and key management.

Exports:
    WalletConfig    — wraps private key + RPC into a signing-ready object
    load_wallet     — load wallet dict from vault/wallet.json
    send_transaction — sign and broadcast an ETH transfer
"""

from vault.wallet_config import WalletConfig
from vault.wallet_manager import load_wallet, send_transaction

__all__ = ["WalletConfig", "load_wallet", "send_transaction"]
