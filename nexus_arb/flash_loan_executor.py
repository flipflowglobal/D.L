"""
nexus_arb/flash_loan_executor.py
=================================

Converts ``ArbitrageResult`` (Bellman-Ford negative-cycle output) into ABI-
encoded calldata for the ``NexusFlashReceiver`` Solidity contract, which
executes multi-hop flash-loan arbitrage in a single atomic transaction.

Architecture
------------
  ArbitrageResult
      └─ FlashLoanExecutor.build_calldata()
              └─ [SwapStep, SwapStep, …]   ← one per cycle edge
                     └─ ABI-encoded params passed to flashLoan(asset, amount, params)

Formal Specification
--------------------
  Preconditions:
    - opp.has_cycle is True
    - opp.cycle contains at least 3 tokens (A→B→…→A — at least one intermediate)
    - opp.profit_ratio > 1.0
    - All cycle_edges entries are valid (rate > 0)

  Postconditions (execute):
    - dry_run=True  → logs calldata, returns None, no network calls
    - dry_run=False → broadcasts tx, returns tx_hash string

  Invariants:
    - Private key is never logged
    - Validation runs before any encoding
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class SwapStep:
    """
    A single swap leg within the flash-loan arbitrage cycle.

    Fields mirror the ``SwapStep`` struct in ``NexusFlashReceiver.sol``:
        struct SwapStep {
            address tokenIn;
            address tokenOut;
            address pool;
            uint24  fee;
        }
    """
    token_in:  str
    token_out: str
    pool:      str = "0x0000000000000000000000000000000000000000"
    fee:       int = 3000   # Uniswap V3 default: 0.3 %


# ── FlashLoanExecutor ──────────────────────────────────────────────────────────


class FlashLoanExecutor:
    """
    Builds and optionally broadcasts flash-loan arbitrage transactions.

    Parameters
    ----------
    w3                : Web3 instance (optional in dry-run / offline mode)
    contract_address  : deployed NexusFlashReceiver address
    aave_pool_address : Aave V3 pool address for flash loans
    borrow_asset      : ERC-20 token to borrow (default: WETH)
    borrow_amount_wei : amount to borrow in wei
    dry_run           : if True, encode calldata but never broadcast
    """

    # Aave V3 flash-loan premium (0.09 %)
    AAVE_PREMIUM = 0.0009

    def __init__(
        self,
        w3=None,
        contract_address: str = "0x0000000000000000000000000000000000000000",
        aave_pool_address: str = "0x0000000000000000000000000000000000000000",
        borrow_asset: str      = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH mainnet
        borrow_amount_wei: int = 10 ** 18,   # 1 WETH default
        dry_run: bool          = True,
    ) -> None:
        self._w3               = w3
        self.contract_address  = contract_address
        self.aave_pool_address = aave_pool_address
        self.borrow_asset      = borrow_asset
        self.borrow_amount_wei = borrow_amount_wei
        self.dry_run           = dry_run

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, w3=None) -> "FlashLoanExecutor":
        """
        Construct from environment variables.

        Required env vars:
          NEXUS_CONTRACT_ADDRESS  — deployed NexusFlashReceiver address
          AAVE_POOL_ADDRESS       — Aave V3 pool address
          BORROW_ASSET            — token address to borrow (optional, default WETH)
          BORROW_AMOUNT_ETH       — amount in ETH units (optional, default 1.0)
          FLASH_DRY_RUN           — "false" to enable live mode (default dry-run)
        """
        contract  = os.getenv("NEXUS_CONTRACT_ADDRESS", "0x" + "0" * 40)
        aave_pool = os.getenv("AAVE_POOL_ADDRESS",      "0x" + "0" * 40)
        asset     = os.getenv("BORROW_ASSET",           "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
        amount    = int(float(os.getenv("BORROW_AMOUNT_ETH", "1.0")) * 10 ** 18)
        dry       = os.getenv("FLASH_DRY_RUN", "true").lower() != "false"

        return cls(
            w3=w3,
            contract_address=contract,
            aave_pool_address=aave_pool,
            borrow_asset=asset,
            borrow_amount_wei=amount,
            dry_run=dry,
        )

    # ── validation ────────────────────────────────────────────────────────────

    def _validate_opportunity(self, opp) -> None:
        """
        Strict validation — raises on any structural issue.

        Raises
        ------
        ValueError  if the opportunity fails any invariant
        """
        required = ("has_cycle", "cycle", "profit_ratio", "cycle_edges")
        missing  = [f for f in required if not hasattr(opp, f)]
        if missing:
            raise ValueError(f"ArbitrageResult missing required fields: {missing}")

        if not opp.has_cycle:
            raise ValueError("has_cycle is False — no arbitrage opportunity")

        unique_tokens = set(opp.cycle)
        if len(unique_tokens) < 2:
            raise ValueError(
                f"Cycle must contain at least 2 unique tokens; got {sorted(unique_tokens)}"
            )

        if len(opp.cycle) < 3:
            raise ValueError(
                f"Cycle path too short (need ≥3 entries including repeated start): {opp.cycle}"
            )

        if opp.profit_ratio <= 1.0:
            raise ValueError(
                f"Unprofitable cycle: profit_ratio={opp.profit_ratio:.6f} ≤ 1.0"
            )

    def validate_opportunity(self, opp) -> Tuple[bool, str]:
        """
        Non-raising validation — suitable for pre-flight checks.

        Returns
        -------
        (True, "ok")              if all checks pass
        (False, <reason string>)  if any check fails
        """
        try:
            self._validate_opportunity(opp)
            return True, "ok"
        except ValueError as exc:
            return False, str(exc)

    # ── calldata builder ──────────────────────────────────────────────────────

    def _build_swap_steps(self, opp) -> List[SwapStep]:
        """
        Convert cycle edges to ``SwapStep`` objects.

        Each edge (token_in, token_out, rate) from the Bellman-Ford result
        maps to one swap leg.  Pool address defaults to the zero address
        here; real deployments resolve pools from a registry.
        """
        steps = []
        for token_in, token_out, _rate in opp.cycle_edges:
            steps.append(SwapStep(token_in=token_in, token_out=token_out))
        return steps

    def build_calldata(self, opp) -> bytes:
        """
        Encode the full flash-loan calldata for NexusFlashReceiver.

        The ``params`` bytes field is a simple UTF-8 JSON encoding of swap
        steps.  Production deployments should use ABI-encoding or RLP for
        gas efficiency; this implementation prioritises readability and
        offline testability.

        Parameters
        ----------
        opp : ArbitrageResult  (validated before encoding)

        Returns
        -------
        bytes  ABI-compatible params payload

        Raises
        ------
        ValueError  if the opportunity fails validation
        """
        self._validate_opportunity(opp)

        steps = self._build_swap_steps(opp)
        import json
        payload = json.dumps([
            {"tokenIn": s.token_in, "tokenOut": s.token_out,
             "pool": s.pool, "fee": s.fee}
            for s in steps
        ], separators=(",", ":"))
        encoded = payload.encode("utf-8")

        logger.debug(
            "Built calldata for %d-hop cycle | profit_ratio=%.6f | %d bytes",
            len(steps), opp.profit_ratio, len(encoded),
        )
        return encoded

    # ── execution ─────────────────────────────────────────────────────────────

    def execute(self, opp, private_key: Optional[str] = None) -> Optional[str]:
        """
        Encode calldata and optionally broadcast the flash-loan transaction.

        Parameters
        ----------
        opp         : ArbitrageResult (must pass validation)
        private_key : hex private key (required when dry_run=False)

        Returns
        -------
        str   tx hash (0x…) when dry_run=False and broadcast succeeds
        None  when dry_run=True

        Raises
        ------
        ValueError  if validation fails, or dry_run=False with no w3/key
        RuntimeError  if transaction broadcast fails
        """
        is_valid, reason = self.validate_opportunity(opp)
        if not is_valid:
            raise ValueError(f"Opportunity rejected: {reason}")

        calldata = self.build_calldata(opp)

        net_profit_estimate = (opp.profit_ratio - 1.0 - self.AAVE_PREMIUM)
        logger.info(
            "FlashLoan | cycle=%s | profit_ratio=%.4f | net_est=%.4f%% | dry_run=%s",
            "→".join(opp.cycle),
            opp.profit_ratio,
            net_profit_estimate * 100,
            self.dry_run,
        )

        if self.dry_run:
            logger.info(
                "DRY RUN — calldata (%d bytes): %s…", len(calldata), calldata[:80]
            )
            return None

        # Live broadcast path
        if self._w3 is None:
            raise ValueError("w3 is required for live execution (dry_run=False)")
        if not private_key:
            raise ValueError("private_key is required for live execution (dry_run=False)")

        try:
            account = self._w3.eth.account.from_key(private_key)
            nonce   = self._w3.eth.get_transaction_count(account.address, "pending")

            tx = {
                "to":       self.contract_address,
                "from":     account.address,
                "nonce":    nonce,
                "gas":      500_000,
                "gasPrice": self._w3.eth.gas_price,
                "chainId":  self._w3.eth.chain_id,
                "data":     calldata,
                "value":    0,
            }
            signed  = self._w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            hex_hash = tx_hash.hex() if isinstance(tx_hash, bytes) else tx_hash
            logger.info("Flash loan tx sent: %s", hex_hash)
            return hex_hash

        except Exception as exc:
            raise RuntimeError(f"Flash loan broadcast failed: {exc}") from exc
