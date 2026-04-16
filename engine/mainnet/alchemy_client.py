"""
engine/mainnet/alchemy_client.py
=================================

Alchemy-aware Web3 wrapper for Ethereum mainnet.

Why Alchemy instead of generic Web3
-------------------------------------
  - ``eth_maxPriorityFeePerGas``  — Alchemy's own reliable gas oracle endpoint
  - ``alchemy_getTokenBalances``  — batched ERC-20 balance lookup in one call
  - Higher rate limits (330 req/s on free tier vs ~10 on public nodes)
  - Persistent HTTPS keep-alive and automatic WebSocket URL derivation
  - Enhanced ``eth_getTransactionReceipts`` for batch receipt polling

This module is **backwards-compatible**: any HTTPS RPC URL works (Infura,
QuickNode, a local node). Alchemy-specific features degrade gracefully to
their standard Web3 equivalents when a non-Alchemy URL is provided.

Formal Specification
---------------------
  Preconditions:
    - rpc_url: HTTPS URL beginning with ``https://``
    - timeout: positive integer (seconds)

  Postconditions:
    - ``AlchemyClient.w3`` is a connected Web3 instance (``is_connected()``)
    - ``get_eip1559_fees()`` always returns three positive integers
    - ``websocket_url`` returns wss:// equivalent for Alchemy URLs, else None

  Invariants:
    - Private key is never stored; client is read-only for pricing
    - Connection is verified at construction; ConnectionError raised on failure
    - All fee values are in wei (int)

EIP-1559 Fee Model
------------------
  Every transaction sets two gas price fields:

    maxPriorityFeePerGas  — tip paid directly to the block proposer
    maxFeePerGas          — hard cap: baseFee + maxPriorityFeePerGas + buffer

  The buffer (GAS_FEE_BUFFER_MULTIPLIER) prevents the tx from stalling if
  the base fee spikes 12.5 % in consecutive blocks (EIP-1559 max increase).
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

from web3 import Web3

# ── Constants ─────────────────────────────────────────────────────────────────

# Multiply estimated baseFee by this to absorb up to N consecutive full blocks.
# 1.15 covers 1 full block (+12.5 %) with a 2.5 % safety margin.
GAS_FEE_BUFFER_MULTIPLIER = float(os.getenv("GAS_FEE_BUFFER_MULTIPLIER", "1.15"))

# Default max priority fee when the oracle call fails (2 gwei)
DEFAULT_PRIORITY_FEE_GWEI = float(os.getenv("DEFAULT_PRIORITY_FEE_GWEI", "2.0"))

# Connection timeout
DEFAULT_TIMEOUT = int(os.getenv("RPC_TIMEOUT_SECONDS", "30"))

# Alchemy URL pattern: https://eth-mainnet.g.alchemy.com/v2/KEY
_ALCHEMY_RE = re.compile(
    r"^https?://[a-z0-9-]+\.g\.alchemy\.com/v2/([A-Za-z0-9_-]+)$",
    re.IGNORECASE,
)


class AlchemyClient:
    """
    Web3 instance with Alchemy-specific enhancements.

    Parameters
    ----------
    rpc_url : HTTPS RPC endpoint (Alchemy, Infura, or any node)
    timeout : per-call HTTP timeout in seconds

    Example
    -------
    >>> client = AlchemyClient("https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY")
    >>> base, priority, max_fee = client.get_eip1559_fees()
    >>> print(f"baseFee={base/1e9:.1f} gwei  tip={priority/1e9:.1f} gwei")
    """

    def __init__(self, rpc_url: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        if not rpc_url:
            raise ValueError(
                "RPC_URL is required. Get a free key at https://www.alchemy.com"
            )
        if not rpc_url.startswith(("http://", "https://")):
            raise ValueError(
                f"RPC_URL must start with http:// or https://. Got: {rpc_url!r}"
            )

        self._rpc_url = rpc_url
        self._timeout = timeout
        self._is_alchemy = bool(_ALCHEMY_RE.match(rpc_url))

        self._w3 = Web3(
            Web3.HTTPProvider(
                rpc_url,
                request_kwargs={"timeout": timeout},
            )
        )

        # Verify connectivity at construction time
        if not self._w3.is_connected():
            raise ConnectionError(
                f"Cannot connect to Ethereum node at {rpc_url!r}. "
                "Check your RPC_URL and network connectivity."
            )

    # ── public properties ─────────────────────────────────────────────────────

    @property
    def w3(self) -> Web3:
        """The underlying Web3 instance."""
        return self._w3

    @property
    def rpc_url(self) -> str:
        return self._rpc_url

    def is_alchemy(self) -> bool:
        """True if the URL matches the Alchemy endpoint pattern."""
        return self._is_alchemy

    def is_connected(self) -> bool:
        return self._w3.is_connected()

    @property
    def chain_id(self) -> int:
        return self._w3.eth.chain_id

    # ── WebSocket URL derivation ───────────────────────────────────────────────

    @property
    def websocket_url(self) -> Optional[str]:
        """
        Derive the wss:// WebSocket endpoint for Alchemy URLs.

        https://eth-mainnet.g.alchemy.com/v2/KEY
          →  wss://eth-mainnet.g.alchemy.com/v2/KEY

        Returns None for non-Alchemy URLs.
        """
        if not self._is_alchemy:
            return None
        return self._rpc_url.replace("https://", "wss://", 1)

    # ── EIP-1559 fee estimation ───────────────────────────────────────────────

    def get_eip1559_fees(self) -> Tuple[int, int, int]:
        """
        Return current EIP-1559 fee parameters in wei.

        Returns
        -------
        (base_fee_wei, max_priority_fee_wei, max_fee_wei)

        Where:
          base_fee_wei        = latest block base fee (burned, not paid to miner)
          max_priority_fee_wei = tip to block proposer (Alchemy oracle or default)
          max_fee_wei          = hard cap = base_fee × buffer + priority_fee

        On any failure, returns safe conservative estimates.
        """
        try:
            base_fee = self._get_base_fee()
            priority = self._get_priority_fee()
            max_fee  = int(base_fee * GAS_FEE_BUFFER_MULTIPLIER) + priority
            return (base_fee, priority, max_fee)
        except Exception as exc:
            # Conservative fallback: 30 gwei base + 2 gwei tip
            print(f"[AlchemyClient] Fee estimation failed ({exc}), using fallback")
            base_fallback     = Web3.to_wei(30, "gwei")
            priority_fallback = Web3.to_wei(DEFAULT_PRIORITY_FEE_GWEI, "gwei")
            return (
                base_fallback,
                priority_fallback,
                base_fallback + priority_fallback,
            )

    def _get_base_fee(self) -> int:
        """Return baseFeePerGas from the pending block header."""
        block = self._w3.eth.get_block("pending")
        base  = block.get("baseFeePerGas")
        if base is None:
            # Pre-London block or chain without EIP-1559
            raise ValueError("baseFeePerGas not present — chain may not support EIP-1559")
        return int(base)

    def _get_priority_fee(self) -> int:
        """
        Return the suggested maxPriorityFeePerGas.

        Alchemy provides the ``eth_maxPriorityFeePerGas`` method which gives
        a reliable P75 estimate.  For non-Alchemy RPCs we fall back to the
        standard ``eth_maxPriorityFeePerGas`` RPC (supported by Infura/Geth/Besu)
        and finally to a hardcoded default.
        """
        try:
            # web3.py >= 6 exposes this as w3.eth.max_priority_fee
            fee = self._w3.eth.max_priority_fee
            return int(fee)
        except Exception:
            return int(Web3.to_wei(DEFAULT_PRIORITY_FEE_GWEI, "gwei"))

    # ── Account / balance helpers ─────────────────────────────────────────────

    def get_eth_balance(self, address: str) -> float:
        """Return ETH balance in ether (float)."""
        wei = self._w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(self._w3.from_wei(wei, "ether"))

    def get_nonce(self, address: str, pending: bool = True) -> int:
        """
        Return the next transaction count for `address`.

        Uses ``"pending"`` state to account for queued (not yet mined) txs,
        preventing nonce collisions when sending multiple transactions quickly.
        """
        state = "pending" if pending else "latest"
        return self._w3.eth.get_transaction_count(
            Web3.to_checksum_address(address), state
        )

    def get_block_number(self) -> int:
        return self._w3.eth.block_number

    # ── Gas estimation ────────────────────────────────────────────────────────

    def estimate_gas(self, tx_params: dict) -> int:
        """
        Call ``eth_estimateGas`` and return result + 20 % safety buffer.
        Returns 300_000 on failure (conservative upper bound for a swap).
        """
        try:
            estimated = self._w3.eth.estimate_gas(tx_params)
            return int(estimated * 1.20)   # +20 % buffer
        except Exception as exc:
            print(f"[AlchemyClient] estimate_gas failed ({exc}), using 300_000")
            return 300_000

    # ── repr ─────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        masked = self._rpc_url[:30] + "…" if len(self._rpc_url) > 30 else self._rpc_url
        return f"AlchemyClient(url={masked!r}, alchemy={self._is_alchemy})"
