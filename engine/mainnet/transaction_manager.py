# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
TransactionManager — production EIP-1559 transaction lifecycle manager.

Responsibilities:
  - Thread-safe nonce management (prevents nonce collisions on concurrent txs)
  - Build and sign EIP-1559 (type-2) transactions
  - Broadcast and wait for receipt with configurable timeout
  - Automatic gas-price bump for stuck (unconfirmed) transactions
  - Detailed logging of every submitted transaction
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from web3 import Web3
from web3.types import TxReceipt

from .alchemy_client import AlchemyClient, EIP1559Fees

log = logging.getLogger(__name__)

# Timing defaults
_POLL_INTERVAL_S    = 2.0    # How often to poll for receipt
_DEFAULT_TIMEOUT_S  = 120    # Max seconds to wait for confirmation
_GAS_BUMP_PERCENT   = 15     # Increase maxFee/priorityFee by 15 % on retry
_MAX_RETRY_BUMPS    = 3      # Maximum gas-bump retries before giving up


class TransactionReceipt:
    """Enriched receipt with derived fields."""

    def __init__(self, raw: TxReceipt, gas_price_wei: int, eth_price_usd: float = 0.0) -> None:
        self.raw            = raw
        self.tx_hash        = raw["transactionHash"].hex()
        self.block_number   = raw["blockNumber"]
        self.gas_used       = raw["gasUsed"]
        self.status         = raw["status"]   # 1=success, 0=reverted
        self.success        = raw["status"] == 1
        self.gas_cost_wei   = self.gas_used * gas_price_wei
        self.gas_cost_eth   = self.gas_cost_wei / 1e18
        self.gas_cost_usd   = self.gas_cost_eth * eth_price_usd if eth_price_usd else 0.0

    def __repr__(self) -> str:
        status = "SUCCESS" if self.success else "REVERTED"
        return (
            f"TransactionReceipt({status} tx={self.tx_hash[:18]}… "
            f"block={self.block_number} gas={self.gas_used:,})"
        )


class NonceTooLowError(Exception):
    pass


class TransactionRevertedError(Exception):
    pass


class TransactionTimeoutError(Exception):
    pass


class TransactionManager:
    """
    Thread-safe EIP-1559 transaction lifecycle manager.

    Usage:
        mgr = TransactionManager(w3, account, alchemy_client)

        # Simple ETH transfer
        receipt = mgr.send_transaction(
            to="0xRecipient",
            value_wei=w3.to_wei(0.01, "ether"),
            gas_limit=21_000,
        )

        # Contract function call
        call_data = contract.functions.myFunc(args).build_transaction({"from": address})
        receipt = mgr.send_transaction_raw(call_data)
    """

    def __init__(
        self,
        w3: Web3,
        account,                         # LocalAccount from eth_account
        alchemy_client: Optional[AlchemyClient] = None,
        eth_price_usd: float = 0.0,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self.w3              = w3
        self.account         = account
        self.alchemy         = alchemy_client
        self.eth_price_usd   = eth_price_usd
        self.timeout_s       = timeout_s
        self.poll_interval_s = poll_interval_s
        self._nonce_lock     = threading.Lock()
        self._pending_nonce: Optional[int] = None

    # ── Nonce management ──────────────────────────────────────────────────────

    def _next_nonce(self) -> int:
        """
        Return the next nonce to use, tracking any pending (unconfirmed) nonces.
        Thread-safe via a lock so concurrent callers don't reuse the same nonce.
        """
        with self._nonce_lock:
            on_chain = self.w3.eth.get_transaction_count(
                self.account.address, block_identifier="pending"
            )
            if self._pending_nonce is None or on_chain > self._pending_nonce:
                self._pending_nonce = on_chain
            nonce = self._pending_nonce
            self._pending_nonce += 1
            return nonce

    def _release_nonce(self) -> None:
        """Call if a tx was never broadcast (error before send), so next call re-reads chain."""
        with self._nonce_lock:
            self._pending_nonce = None

    # ── Fee estimation ────────────────────────────────────────────────────────

    def _get_fees(self) -> EIP1559Fees:
        if self.alchemy:
            return self.alchemy.get_eip1559_fees()
        # Fallback: derive from w3 directly
        try:
            block = self.w3.eth.get_block("latest")
            base_fee = int(block.get("baseFeePerGas", 20 * int(1e9)))
            priority = int(self.w3.eth.max_priority_fee)
            max_fee  = base_fee * 2 + priority
            from .alchemy_client import EIP1559Fees
            return EIP1559Fees(
                max_priority_fee_wei=priority,
                max_fee_per_gas_wei=max_fee,
                base_fee_wei=base_fee,
            )
        except Exception:
            from .alchemy_client import EIP1559Fees
            return EIP1559Fees(
                max_priority_fee_wei=int(1.5e9),
                max_fee_per_gas_wei=int(50e9),
                base_fee_wei=int(20e9),
            )

    @staticmethod
    def _bump_fees(fees: EIP1559Fees, pct: int = _GAS_BUMP_PERCENT) -> EIP1559Fees:
        """Increase fees by `pct` percent for gas-bump retry."""
        from .alchemy_client import EIP1559Fees
        mult = 1 + pct / 100
        return EIP1559Fees(
            max_priority_fee_wei=int(fees.max_priority_fee_wei * mult),
            max_fee_per_gas_wei=int(fees.max_fee_per_gas_wei * mult),
            base_fee_wei=fees.base_fee_wei,
        )

    # ── Build transaction ─────────────────────────────────────────────────────

    def _build_tx(
        self,
        to: str,
        value_wei: int,
        data: bytes,
        gas_limit: int,
        fees: EIP1559Fees,
        nonce: int,
    ) -> dict:
        """Assemble an EIP-1559 (type-2) transaction dict."""
        return {
            "type":                 2,
            "chainId":              self.w3.eth.chain_id,
            "nonce":                nonce,
            "to":                   Web3.to_checksum_address(to) if to else None,
            "value":                value_wei,
            "data":                 data,
            "gas":                  gas_limit,
            "maxPriorityFeePerGas": fees.max_priority_fee_wei,
            "maxFeePerGas":         fees.max_fee_per_gas_wei,
        }

    # ── Send and confirm ──────────────────────────────────────────────────────

    def send_transaction(
        self,
        to: str,
        value_wei: int = 0,
        data: bytes = b"",
        gas_limit: int = 21_000,
        fees: Optional[EIP1559Fees] = None,
    ) -> TransactionReceipt:
        """
        Build, sign, broadcast an EIP-1559 tx and wait for confirmation.

        Args:
            to:         Recipient or contract address
            value_wei:  ETH to send in wei (default 0)
            data:       ABI-encoded calldata (default empty)
            gas_limit:  Gas limit
            fees:       Override fee estimates

        Returns:
            TransactionReceipt

        Raises:
            TransactionRevertedError if the transaction reverted on-chain.
            TransactionTimeoutError  if not confirmed within timeout_s.
        """
        fees = fees or self._get_fees()
        nonce = self._next_nonce()

        for bump in range(_MAX_RETRY_BUMPS + 1):
            tx_dict = self._build_tx(to, value_wei, data, gas_limit, fees, nonce)
            try:
                signed  = self.account.sign_transaction(tx_dict)
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                log.info(
                    f"[TxMgr] Sent tx={tx_hash.hex()[:18]}… nonce={nonce} "
                    f"maxFee={fees.max_fee_gwei:.2f} gwei"
                )
            except Exception as exc:
                self._release_nonce()
                raise RuntimeError(f"Transaction send failed: {exc}") from exc

            try:
                receipt = self._wait_for_receipt(tx_hash)
                if not receipt.success:
                    raise TransactionRevertedError(
                        f"Transaction reverted: {receipt.tx_hash}"
                    )
                return receipt
            except TransactionTimeoutError:
                if bump < _MAX_RETRY_BUMPS:
                    fees = self._bump_fees(fees)
                    log.warning(
                        f"[TxMgr] Timeout — bumping gas {_GAS_BUMP_PERCENT}% "
                        f"(attempt {bump+1}/{_MAX_RETRY_BUMPS}), "
                        f"new maxFee={fees.max_fee_gwei:.2f} gwei"
                    )
                    # Reuse same nonce to replace the stuck transaction
                else:
                    raise

    def send_transaction_dict(self, tx: dict) -> TransactionReceipt:
        """
        Send a pre-built transaction dict (from contract.functions.X().build_transaction).
        Replaces legacy gasPrice with EIP-1559 fields.
        """
        fees  = self._get_fees()
        nonce = self._next_nonce()

        # Inject EIP-1559 fields, remove legacy gasPrice
        tx = dict(tx)
        tx.pop("gasPrice", None)
        tx["type"]                 = 2
        tx["nonce"]                = nonce
        tx["chainId"]              = self.w3.eth.chain_id
        tx["maxPriorityFeePerGas"] = fees.max_priority_fee_wei
        tx["maxFeePerGas"]         = fees.max_fee_per_gas_wei

        for bump in range(_MAX_RETRY_BUMPS + 1):
            try:
                signed   = self.account.sign_transaction(tx)
                tx_hash  = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                log.info(f"[TxMgr] Sent tx={tx_hash.hex()[:18]}… nonce={nonce}")
            except Exception as exc:
                self._release_nonce()
                raise RuntimeError(f"Transaction send failed: {exc}") from exc

            try:
                receipt = self._wait_for_receipt(tx_hash)
                if not receipt.success:
                    raise TransactionRevertedError(f"Transaction reverted: {receipt.tx_hash}")
                return receipt
            except TransactionTimeoutError:
                if bump < _MAX_RETRY_BUMPS:
                    fees = self._bump_fees(fees)
                    tx["maxPriorityFeePerGas"] = fees.max_priority_fee_wei
                    tx["maxFeePerGas"]         = fees.max_fee_per_gas_wei
                    log.warning(f"[TxMgr] Timeout, bumping gas (attempt {bump+1})")
                else:
                    raise

    def _wait_for_receipt(self, tx_hash: bytes) -> TransactionReceipt:
        """Poll until the transaction is mined or timeout expires."""
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            try:
                raw = self.w3.eth.get_transaction_receipt(tx_hash)
                if raw is not None:
                    # Get effective gas price for cost calc
                    try:
                        gas_price = raw.get("effectiveGasPrice", 0)
                    except Exception:
                        gas_price = 0
                    return TransactionReceipt(raw, gas_price, self.eth_price_usd)
            except Exception:
                pass
            time.sleep(self.poll_interval_s)

        raise TransactionTimeoutError(
            f"Transaction {tx_hash.hex()[:18]}… not confirmed within {self.timeout_s}s"
        )

    # ── Convenience: ERC-20 approval with EIP-1559 ───────────────────────────

    ERC20_APPROVE_ABI = [
        {
            "name": "approve",
            "type": "function",
            "stateMutability": "nonpayable",
            "inputs": [
                {"name": "spender", "type": "address"},
                {"name": "amount",  "type": "uint256"},
            ],
            "outputs": [{"name": "", "type": "bool"}],
        },
        {
            "name": "allowance",
            "type": "function",
            "stateMutability": "view",
            "inputs": [
                {"name": "owner",   "type": "address"},
                {"name": "spender", "type": "address"},
            ],
            "outputs": [{"name": "", "type": "uint256"}],
        },
    ]

    def ensure_erc20_approval(
        self,
        token_address: str,
        spender: str,
        amount_wei: int,
    ) -> Optional[TransactionReceipt]:
        """
        Check ERC-20 allowance and approve if insufficient.
        Returns receipt if an approval tx was sent, None if already approved.
        """
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=self.ERC20_APPROVE_ABI,
        )
        current = token.functions.allowance(
            self.account.address,
            Web3.to_checksum_address(spender),
        ).call()

        if current >= amount_wei:
            return None

        # Reset to 0 first (required by some tokens e.g. USDT)
        if current > 0:
            reset_tx = token.functions.approve(
                Web3.to_checksum_address(spender), 0
            ).build_transaction({"from": self.account.address, "gas": 60_000})
            self.send_transaction_dict(reset_tx)

        approve_tx = token.functions.approve(
            Web3.to_checksum_address(spender),
            2**256 - 1,
        ).build_transaction({"from": self.account.address, "gas": 60_000})
        return self.send_transaction_dict(approve_tx)
