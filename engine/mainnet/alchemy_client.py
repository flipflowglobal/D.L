# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
AlchemyClient — production Ethereum RPC client.

Provides:
  - EIP-1559 fee estimation (baseFee + priority fee)
  - WebSocket URL construction from HTTP Alchemy endpoint
  - Connection health check with automatic retry
  - Alchemy-specific eth_maxPriorityFeePerGas call
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

from web3 import Web3

log = logging.getLogger(__name__)

# Sane defaults if Alchemy fee API is unavailable
_DEFAULT_PRIORITY_FEE_GWEI = 1.5    # "tip" to validator
_DEFAULT_MAX_FEE_MULTIPLIER = 2.0   # baseFee × this = maxFeePerGas headroom
_CONNECT_RETRIES = 3
_CONNECT_BACKOFF = 2.0              # seconds


class EIP1559Fees:
    """Typed result from fee estimation."""

    __slots__ = ("max_priority_fee_wei", "max_fee_per_gas_wei", "base_fee_wei")

    def __init__(
        self,
        max_priority_fee_wei: int,
        max_fee_per_gas_wei: int,
        base_fee_wei: int,
    ) -> None:
        self.max_priority_fee_wei = max_priority_fee_wei
        self.max_fee_per_gas_wei  = max_fee_per_gas_wei
        self.base_fee_wei         = base_fee_wei

    @property
    def max_priority_fee_gwei(self) -> float:
        return self.max_priority_fee_wei / 1e9

    @property
    def max_fee_gwei(self) -> float:
        return self.max_fee_per_gas_wei / 1e9

    @property
    def base_fee_gwei(self) -> float:
        return self.base_fee_wei / 1e9

    def __repr__(self) -> str:
        return (
            f"EIP1559Fees(base={self.base_fee_gwei:.3f} gwei, "
            f"priority={self.max_priority_fee_gwei:.3f} gwei, "
            f"max={self.max_fee_gwei:.3f} gwei)"
        )


class AlchemyClient:
    """
    Web3 wrapper optimised for Alchemy endpoints.

    Features:
      - Derives WebSocket URL from HTTPS Alchemy endpoint automatically
      - EIP-1559 fee oracle using eth_feeHistory + eth_maxPriorityFeePerGas
      - Retry-on-connect for transient network failures
      - is_connected() with automatic reconnect

    Usage:
        client = AlchemyClient(rpc_url="https://eth-mainnet.g.alchemy.com/v2/KEY")
        fees   = client.get_eip1559_fees()
        w3     = client.w3
    """

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        max_priority_fee_gwei: float = _DEFAULT_PRIORITY_FEE_GWEI,
        max_fee_multiplier: float = _DEFAULT_MAX_FEE_MULTIPLIER,
    ) -> None:
        self.http_url             = rpc_url or os.getenv("RPC_URL") or os.getenv("ETH_RPC") or ""
        self.max_priority_fee_gwei = max_priority_fee_gwei
        self.max_fee_multiplier   = max_fee_multiplier
        self._w3: Optional[Web3]  = None

        if self.http_url:
            self._connect()

    # ── WebSocket URL derivation ───────────────────────────────────────────────

    @property
    def ws_url(self) -> Optional[str]:
        """
        Derive WebSocket URL from an Alchemy HTTPS endpoint.

        https://eth-mainnet.g.alchemy.com/v2/KEY
          → wss://eth-mainnet.g.alchemy.com/v2/KEY

        https://eth-mainnet.infura.io/v3/KEY
          → wss://eth-mainnet.infura.io/ws/v3/KEY   (Infura pattern)
        """
        url = self.http_url
        if not url:
            return None

        # Parse the host to avoid incomplete-substring false matches
        # (e.g. "evilalchemy.com" must not match "alchemy.com")
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
        except Exception:
            host = ""

        # Alchemy: simple http→wss scheme swap
        if host == "eth-mainnet.g.alchemy.com" or host.endswith(".alchemy.com"):
            return re.sub(r"^https?://", "wss://", url)

        # Infura: insert /ws/ before /v3/
        if host == "mainnet.infura.io" or host.endswith(".infura.io"):
            url = re.sub(r"^https?://", "wss://", url)
            url = re.sub(r"/v3/", "/ws/v3/", url)
            return url

        # Generic: just swap scheme
        return re.sub(r"^https?://", "wss://", url)

    # ── Connection management ─────────────────────────────────────────────────

    def _connect(self) -> None:
        """Attempt to connect with retries."""
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                self._w3 = Web3(Web3.HTTPProvider(self.http_url, request_kwargs={"timeout": 10}))
                if self._w3.is_connected():
                    log.debug(f"AlchemyClient connected to {self.http_url[:50]}…")
                    return
            except Exception as exc:
                log.warning(f"AlchemyClient connect attempt {attempt} failed: {exc}")
            if attempt < _CONNECT_RETRIES:
                time.sleep(_CONNECT_BACKOFF * attempt)

        log.warning("AlchemyClient: all connection attempts failed — running without RPC")

    @property
    def w3(self) -> Web3:
        """Return connected Web3 instance, reconnecting if needed."""
        if self._w3 is None or not self._w3.is_connected():
            if self.http_url:
                self._connect()
        if self._w3 is None:
            raise RuntimeError("AlchemyClient: no Web3 connection available")
        return self._w3

    def is_connected(self) -> bool:
        """Return True if the RPC endpoint is reachable."""
        try:
            return bool(self._w3 and self._w3.is_connected())
        except Exception:
            return False

    # ── EIP-1559 fee oracle ───────────────────────────────────────────────────

    def get_eip1559_fees(self, priority_fee_percentile: int = 50) -> EIP1559Fees:
        """
        Estimate EIP-1559 transaction fees.

        Algorithm:
          1. Fetch latest block's baseFeePerGas
          2. Get recommended maxPriorityFeePerGas:
             - Try eth_maxPriorityFeePerGas (Alchemy / Geth 1.10+)
             - Fall back to eth_feeHistory median
             - Fall back to configured default
          3. maxFeePerGas = baseFee × multiplier + priorityFee

        Returns:
            EIP1559Fees with all three fee components in wei.
        """
        try:
            w3 = self.w3
            # 1. Base fee from latest block
            block = w3.eth.get_block("latest")
            base_fee_wei = int(block.get("baseFeePerGas", 0))

            # 2. Priority fee
            priority_wei = self._get_priority_fee_wei(w3, priority_fee_percentile)

            # 3. Max fee = 2× baseFee + priority (standard EIP-1559 headroom)
            max_fee_wei = int(base_fee_wei * self.max_fee_multiplier) + priority_wei

            fees = EIP1559Fees(
                max_priority_fee_wei=priority_wei,
                max_fee_per_gas_wei=max_fee_wei,
                base_fee_wei=base_fee_wei,
            )
            log.debug(f"EIP-1559 fees: {fees}")
            return fees

        except Exception as exc:
            log.warning(f"get_eip1559_fees failed ({exc}), using defaults")
            return self._default_fees()

    def _get_priority_fee_wei(self, w3: Web3, percentile: int) -> int:
        """
        Try three approaches (best to worst) to get the miner tip:
          1. eth_maxPriorityFeePerGas  (Alchemy / Geth 1.10+, fastest)
          2. eth_feeHistory median     (EIP-1559 standard)
          3. Configured default
        """
        # Approach 1: Alchemy native call
        try:
            result = w3.eth.max_priority_fee
            if result and result > 0:
                return int(result)
        except Exception:
            pass

        # Approach 2: feeHistory
        try:
            history = w3.eth.fee_history(
                block_count=10,
                newest_block="latest",
                reward_percentiles=[percentile],
            )
            rewards = [r[0] for r in history.get("reward", []) if r]
            if rewards:
                return int(sorted(rewards)[len(rewards) // 2])
        except Exception:
            pass

        # Approach 3: default
        return int(self.max_priority_fee_gwei * 1e9)

    def _default_fees(self) -> EIP1559Fees:
        """Return conservative fallback fees when RPC is unavailable."""
        priority_wei = int(self.max_priority_fee_gwei * 1e9)
        base_fee_wei = int(20 * 1e9)   # 20 gwei — conservative mainnet estimate
        max_fee_wei  = int(base_fee_wei * self.max_fee_multiplier) + priority_wei
        return EIP1559Fees(
            max_priority_fee_wei=priority_wei,
            max_fee_per_gas_wei=max_fee_wei,
            base_fee_wei=base_fee_wei,
        )

    # ── Convenience helpers ───────────────────────────────────────────────────

    def get_chain_id(self) -> int:
        return self.w3.eth.chain_id

    def get_block_number(self) -> int:
        return self.w3.eth.block_number

    def get_eth_balance(self, address: str) -> float:
        """Return ETH balance in ether (float)."""
        bal_wei = self.w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(self.w3.from_wei(bal_wei, "ether"))
