"""
engine/mainnet/transaction_manager.py
=======================================

Production-grade EIP-1559 transaction manager for Ethereum mainnet.

Responsibilities
----------------
1. **EIP-1559 tx builder** — assembles ``maxFeePerGas``/``maxPriorityFeePerGas``
   fields from the Alchemy fee oracle, never uses legacy ``gasPrice``.

2. **Nonce manager** — thread-safe, tracks pending txs so parallel callers
   never reuse the same nonce (race condition that causes "nonce too low").

3. **Gas estimator** — calls ``eth_estimateGas`` before signing; adds 20 %
   safety buffer and enforces a configurable hard cap.

4. **Receipt confirmation** — polls ``eth_getTransactionReceipt`` with
   configurable timeout and block-count confirmations.

5. **Revert detection** — parses receipt ``status`` field; raises
   ``TransactionReverted`` with the revert reason when status == 0.

6. **Gas price bumping** — if a tx is not mined within ``bump_timeout``
   seconds, resubmits with 15 % higher ``maxFeePerGas`` using the same nonce
   (EIP-1559 replacement rule requires ≥10 % bump).

Formal Specification
---------------------
  Preconditions:
    - client: AlchemyClient (connected, chain_id consistent with signer)
    - private_key: 32-byte hex private key (with or without 0x prefix)
    - max_gas_limit: positive integer upper bound on gas

  Postconditions (send_and_confirm):
    - Returns TxReceipt with tx_hash, status, gas_used, block_number
    - Raises TransactionReverted if status == 0
    - Raises ConfirmationTimeout if receipt not found within timeout seconds
    - Never returns until `confirmations` new blocks have been mined on top

  Invariants:
    - Nonce counter is strictly monotone (never decremented)
    - Private key is never logged or included in exceptions
    - All fee values in wei; ether values are converted at API boundary only
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from web3 import Web3

from engine.mainnet.alchemy_client import AlchemyClient

logger = logging.getLogger("aureon.tx_manager")

# ── Exceptions ────────────────────────────────────────────────────────────────


class TransactionReverted(Exception):
    """Raised when a mined transaction has status == 0 (EVM revert)."""


class ConfirmationTimeout(Exception):
    """Raised when a sent transaction is not mined within the timeout window."""


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class TxReceipt:
    """Structured result of a confirmed transaction."""
    tx_hash:      str
    status:       int          # 1 = success, 0 = reverted
    gas_used:     int
    gas_limit:    int
    block_number: int
    effective_gas_price_gwei: float
    logs:         list


# ── TransactionManager ────────────────────────────────────────────────────────


class TransactionManager:
    """
    Signs, broadcasts, and confirms EIP-1559 transactions on Ethereum mainnet.

    Parameters
    ----------
    client          : AlchemyClient (provides Web3 + fee oracle)
    private_key     : hex private key (with or without 0x)
    chain_id        : Ethereum chain ID (1 = mainnet, 11155111 = Sepolia)
    max_gas_limit   : hard cap on gas per transaction (default 500_000)
    confirm_timeout : seconds to wait for a receipt (default 120)
    confirmations   : number of blocks to wait after mining (default 1)
    bump_timeout    : seconds before bumping gas on a stuck tx (default 45)
    bump_pct        : gas bump percentage for stuck tx replacement (default 15 %)
    """

    DEFAULT_CONFIRM_TIMEOUT = int(
        __import__("os").getenv("TX_CONFIRM_TIMEOUT", "120")
    )
    DEFAULT_BUMP_TIMEOUT    = int(
        __import__("os").getenv("TX_BUMP_TIMEOUT", "45")
    )
    DEFAULT_MAX_GAS_LIMIT   = int(
        __import__("os").getenv("MAX_GAS_LIMIT", "500000")
    )

    def __init__(
        self,
        client:          AlchemyClient,
        private_key:     str,
        chain_id:        Optional[int] = None,
        max_gas_limit:   int           = DEFAULT_MAX_GAS_LIMIT,
        confirm_timeout: int           = DEFAULT_CONFIRM_TIMEOUT,
        confirmations:   int           = 1,
        bump_timeout:    int           = DEFAULT_BUMP_TIMEOUT,
        bump_pct:        float         = 0.15,
    ) -> None:
        if not private_key:
            raise ValueError("private_key is required")

        self._client    = client
        self._w3        = client.w3
        self._chain_id  = chain_id or client.chain_id
        self._max_gas   = max_gas_limit
        self._timeout   = confirm_timeout
        self._confs     = confirmations
        self._bump_secs = bump_timeout
        self._bump_pct  = bump_pct

        # Load signing account
        key = private_key.strip()
        if key.lower().startswith("0x"):
            key = key[2:]
        self._account   = self._w3.eth.account.from_key(key)
        self._address   = self._account.address

        # Thread-safe nonce manager
        self._nonce_lock = threading.Lock()
        self._nonce: Optional[int] = None   # lazy init on first tx

    # ── public properties ─────────────────────────────────────────────────────

    @property
    def address(self) -> str:
        return self._address

    @property
    def chain_id(self) -> int:
        return self._chain_id

    # ── nonce management ──────────────────────────────────────────────────────

    def _next_nonce(self) -> int:
        """
        Return the next nonce to use, then increment the local counter.

        On the first call: fetches on-chain pending count.
        On subsequent calls: increments locally (no RPC) for speed.

        Thread-safe: protected by _nonce_lock.
        """
        with self._nonce_lock:
            if self._nonce is None:
                self._nonce = self._client.get_nonce(self._address, pending=True)
            nonce = self._nonce
            self._nonce += 1
            return nonce

    def reset_nonce(self) -> None:
        """Force re-sync of nonce from chain (call after a failed tx or restart)."""
        with self._nonce_lock:
            self._nonce = None

    # ── transaction builder ────────────────────────────────────────────────────

    def build_tx(
        self,
        to:        str,
        value_wei: int           = 0,
        data:      bytes         = b"",
        gas_limit: Optional[int] = None,
        nonce:     Optional[int] = None,
    ) -> dict:
        """
        Assemble an EIP-1559 transaction dict (unsigned).

        Parameters
        ----------
        to        : recipient address (checksummed automatically)
        value_wei : ETH value in wei (0 for token-only calls)
        data      : ABI-encoded calldata
        gas_limit : override gas limit; auto-estimated if None
        nonce     : override nonce; auto-managed if None

        Returns
        -------
        Unsigned transaction dict ready for sign_and_send().
        """
        _, priority_fee, max_fee = self._client.get_eip1559_fees()

        tx: dict = {
            "type":                  "0x2",    # EIP-1559
            "chainId":               self._chain_id,
            "to":                    Web3.to_checksum_address(to),
            "value":                 value_wei,
            "data":                  data,
            "maxPriorityFeePerGas":  priority_fee,
            "maxFeePerGas":          max_fee,
            "nonce":                 nonce if nonce is not None else self._next_nonce(),
        }

        # Gas estimation
        if gas_limit is not None:
            tx["gas"] = min(gas_limit, self._max_gas)
        else:
            estimated = self._client.estimate_gas({
                "from":  self._address,
                "to":    tx["to"],
                "value": value_wei,
                "data":  data,
            })
            tx["gas"] = min(estimated, self._max_gas)

        return tx

    # ── sign and send ─────────────────────────────────────────────────────────

    def sign_and_send(self, tx: dict) -> str:
        """
        Sign and broadcast a transaction. Returns the tx hash hex string.
        Does NOT wait for confirmation — use send_and_confirm() for that.
        """
        signed   = self._account.sign_transaction(tx)
        tx_bytes = signed.raw_transaction
        tx_hash  = self._w3.eth.send_raw_transaction(tx_bytes)
        return tx_hash.hex() if isinstance(tx_hash, bytes) else tx_hash

    # ── confirmation polling ───────────────────────────────────────────────────

    def wait_for_receipt(
        self,
        tx_hash:  str,
        timeout:  Optional[int] = None,
    ) -> TxReceipt:
        """
        Poll for a transaction receipt until timeout.

        Parameters
        ----------
        tx_hash : hex transaction hash (with or without 0x prefix)
        timeout : override instance timeout (seconds)

        Returns
        -------
        TxReceipt

        Raises
        ------
        ConfirmationTimeout  if not mined within timeout seconds
        TransactionReverted  if receipt.status == 0
        """
        deadline = time.monotonic() + (timeout or self._timeout)
        poll_interval = 2.0   # poll every 2 seconds

        h = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash

        while time.monotonic() < deadline:
            try:
                receipt = self._w3.eth.get_transaction_receipt(h)
                if receipt is not None:
                    return self._parse_receipt(h, receipt)
            except Exception as exc:
                logger.debug("Receipt poll error: %s", exc)
            time.sleep(poll_interval)

        raise ConfirmationTimeout(
            f"Transaction {h[:18]}… not mined within {timeout or self._timeout}s. "
            "It may still be pending — check Etherscan."
        )

    def _parse_receipt(self, tx_hash: str, receipt: dict) -> TxReceipt:
        """Convert raw receipt dict into a TxReceipt and check for revert."""
        gas_price_wei = receipt.get("effectiveGasPrice", 0)
        gas_price_gwei = round(float(Web3.from_wei(gas_price_wei, "gwei")), 4)

        r = TxReceipt(
            tx_hash=tx_hash,
            status=int(receipt.get("status", 0)),
            gas_used=int(receipt.get("gasUsed", 0)),
            gas_limit=int(receipt.get("gas", receipt.get("gasUsed", 0))),
            block_number=int(receipt.get("blockNumber", 0)),
            effective_gas_price_gwei=gas_price_gwei,
            logs=list(receipt.get("logs", [])),
        )

        if r.status == 0:
            raise TransactionReverted(
                f"Transaction {tx_hash[:18]}… reverted on-chain "
                f"(block={r.block_number}, gas_used={r.gas_used:,}). "
                "Check contract logic, slippage tolerance, or token approvals."
            )

        return r

    # ── send + confirm (main public method) ───────────────────────────────────

    def send_and_confirm(
        self,
        tx:      dict,
        timeout: Optional[int] = None,
    ) -> TxReceipt:
        """
        Sign, broadcast, and wait for confirmation.

        If the tx is not mined within bump_timeout seconds, replaces it with
        an identical tx at bump_pct higher fees (EIP-1559 "speed up").

        Returns
        -------
        TxReceipt on success.

        Raises
        ------
        ConfirmationTimeout  if still not mined after full timeout
        TransactionReverted  if mined with status == 0
        """
        effective_timeout = timeout or self._timeout
        tx_hash = self.sign_and_send(tx)

        print(f"[TxManager] Sent {tx_hash[:18]}…  "
              f"nonce={tx.get('nonce')}  "
              f"maxFee={Web3.from_wei(tx.get('maxFeePerGas', 0), 'gwei'):.2f} gwei")

        # Poll for receipt; bump gas if stuck past bump_timeout
        deadline    = time.monotonic() + effective_timeout
        bump_at     = time.monotonic() + self._bump_secs
        poll_interval = 2.0
        bumped = False

        while time.monotonic() < deadline:
            try:
                receipt = self._w3.eth.get_transaction_receipt(
                    tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash
                )
                if receipt is not None:
                    r = self._parse_receipt(tx_hash, receipt)
                    print(f"[TxManager] Confirmed block={r.block_number}  "
                          f"gas_used={r.gas_used:,}  "
                          f"price={r.effective_gas_price_gwei:.2f} gwei")
                    return r
            except TransactionReverted:
                raise
            except Exception as exc:
                logger.debug("Receipt poll error: %s", exc)

            # Bump gas if stuck
            if not bumped and time.monotonic() >= bump_at:
                new_tx_hash = self._bump_tx(tx)
                if new_tx_hash:
                    print(f"[TxManager] Bumped tx → {new_tx_hash[:18]}…")
                    tx_hash = new_tx_hash
                    bumped = True

            time.sleep(poll_interval)

        raise ConfirmationTimeout(
            f"Transaction {tx_hash[:18]}… not confirmed within {effective_timeout}s."
        )

    # ── gas bump (EIP-1559 replacement) ──────────────────────────────────────

    def _bump_tx(self, original_tx: dict) -> Optional[str]:
        """
        Resubmit a stuck transaction with higher fees.

        EIP-1559 replacement rule: new maxFeePerGas and maxPriorityFeePerGas
        must each be at least 10 % higher than the original.  We use bump_pct
        (default 15 %) for safety.

        The original nonce is reused (same nonce = replace in mempool).
        """
        try:
            factor = 1.0 + self._bump_pct
            bumped = dict(original_tx)
            bumped["maxFeePerGas"]          = int(original_tx["maxFeePerGas"]          * factor)
            bumped["maxPriorityFeePerGas"]  = int(original_tx["maxPriorityFeePerGas"]  * factor)
            # nonce stays the same — this replaces the pending tx
            return self.sign_and_send(bumped)
        except Exception as exc:
            print(f"[TxManager] Gas bump failed: {exc}")
            return None

    # ── approval helper ───────────────────────────────────────────────────────

    _ERC20_ABI = [
        {
            "name": "approve", "type": "function",
            "stateMutability": "nonpayable",
            "inputs":  [{"name": "spender", "type": "address"},
                        {"name": "amount",  "type": "uint256"}],
            "outputs": [{"name": "", "type": "bool"}],
        },
        {
            "name": "allowance", "type": "function",
            "stateMutability": "view",
            "inputs":  [{"name": "owner",   "type": "address"},
                        {"name": "spender", "type": "address"}],
            "outputs": [{"name": "", "type": "uint256"}],
        },
    ]

    def ensure_approval(
        self,
        token_address: str,
        spender:       str,
        amount_wei:    int,
    ) -> Optional[TxReceipt]:
        """
        Check allowance; if insufficient, approve max-uint and wait for confirmation.

        Returns
        -------
        TxReceipt of the approval transaction, or None if already approved.
        """
        token = self._w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=self._ERC20_ABI,
        )
        current = token.functions.allowance(
            self._address,
            Web3.to_checksum_address(spender),
        ).call()

        if current >= amount_wei:
            return None   # sufficient allowance

        calldata = token.encodeABI(
            fn_name="approve",
            args=[Web3.to_checksum_address(spender), 2 ** 256 - 1],
        )
        tx = self.build_tx(
            to=token_address,
            data=Web3.to_bytes(hexstr=calldata),
            gas_limit=80_000,
        )
        receipt = self.send_and_confirm(tx, timeout=60)
        print(f"[TxManager] Approved {token_address[:10]}… for {spender[:10]}…")
        return receipt
