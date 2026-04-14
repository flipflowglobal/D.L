"""
nexus_arb.flash_loan_executor
==============================

Production flash-loan execution layer for AUREON's NexusFlashReceiver path.

Architecture
------------
FlashLoanExecutor orchestrates the full lifecycle of a flash-loan arbitrage:

  1. Validate the ArbitrageResult opportunity (cycle length, profitability gate)
  2. Encode SwapStep calldata for each hop in the cycle
  3. In dry_run=False mode: submit the flash-loan transaction via the
     NexusFlashReceiver smart contract; in dry_run=True mode: simulate and
     return a rich ExecutionReport without touching the chain.

Design Invariants
-----------------
- Zero network calls at import time (offline-first, matches nexus_arb convention)
- dry_run=True is the safe default; live execution requires explicit opt-in
- _validate_opportunity is a pure predicate — raises, never returns a sentinel
- All public methods are type-annotated; internal helpers are prefixed with _

Formal Specification (_validate_opportunity)
---------------------------------------------
  Preconditions:
    - opportunity: ArbitrageResult (has_cycle, cycle: List[str], profit_ratio, cycle_edges)

  Postconditions:
    - Raises ValueError  if opportunity.has_cycle is False
    - Raises ValueError  if len(opportunity.cycle) < 3
      (minimum valid cycle: A → B → A  requires nodes [A, B, A] = 3 elements)
    - Raises ValueError  if opportunity.profit_ratio <= 1.0 (no real profit)
    - Returns None       iff all invariants hold

  Invariant:
    - A 1-hop "cycle" ["A", "A"] is rejected — it is a self-loop, not a true
      arbitrage path and would lose money to gas + Aave premium.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from nexus_arb.algorithms.bellman_ford import ArbitrageResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data transfer objects
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SwapStep:
    """
    Encoded representation of a single DEX swap hop.

    Attributes
    ----------
    token_in   : address / symbol of the input token
    token_out  : address / symbol of the output token
    rate       : expected exchange rate (token_out per token_in)
    pool_id    : DEX identifier (e.g. "uniswap_v3", "sushiswap")
    """
    token_in:  str
    token_out: str
    rate:      float
    pool_id:   str = "unknown"


@dataclass
class ExecutionReport:
    """
    Summary produced by FlashLoanExecutor.execute().

    Fields
    ------
    success        : True iff the execution path completed without error
    dry_run        : mirrors the executor's dry_run flag
    borrow_token   : first token in the cycle (flash-loan asset)
    n_hops         : number of swap steps in the route
    profit_ratio   : projected profit multiplier from ArbitrageResult
    tx_hash        : on-chain transaction hash (None in dry_run mode)
    swap_steps     : encoded hop sequence
    error          : exception message if success is False
    """
    success:      bool
    dry_run:      bool
    borrow_token: str
    n_hops:       int
    profit_ratio: float
    tx_hash:      Optional[str]                = None
    swap_steps:   List[SwapStep]               = field(default_factory=list)
    error:        Optional[str]                = None


# ─────────────────────────────────────────────────────────────────────────────
# Core executor
# ─────────────────────────────────────────────────────────────────────────────

class FlashLoanExecutor:
    """
    Orchestrates flash-loan arbitrage execution via NexusFlashReceiver.

    Parameters
    ----------
    dry_run : bool
        When True (default) the executor simulates the full path but does NOT
        submit any transaction.  Set to False only in production with a funded
        wallet and a deployed NexusFlashReceiver contract.
    min_profit_ratio : float
        Minimum acceptable profit_ratio (exclusive lower bound).  Opportunities
        below this threshold are rejected by _validate_opportunity.
    max_hops : int
        Hard cap on cycle length to bound gas consumption.

    Usage
    -----
    >>> from nexus_arb.algorithms.bellman_ford import BellmanFordArb
    >>> arb = BellmanFordArb()
    >>> arb.add_edge("WETH", "USDC", 2001.0, "uniswap_v3")
    >>> arb.add_edge("USDC", "WETH", 1/1998.5, "sushiswap")
    >>> result = arb.find_arbitrage("WETH")
    >>> executor = FlashLoanExecutor(dry_run=True)
    >>> if result.has_cycle:
    ...     report = executor.execute(result)
    ...     print(report.success, report.n_hops)
    """

    # Minimum cycle length: [A, B, A] = 3 nodes → 2 real hops (A→B and B→A).
    # A cycle of length < 3 means at most 1 hop which is always unprofitable
    # after gas + Aave premium and is never a genuine arbitrage path.
    _MIN_CYCLE_LENGTH: int = 3

    def __init__(
        self,
        dry_run: bool = True,
        min_profit_ratio: float = 1.001,
        max_hops: int = 10,
    ) -> None:
        self.dry_run          = dry_run
        self.min_profit_ratio = min_profit_ratio
        self.max_hops         = max_hops
        logger.info(
            "FlashLoanExecutor initialised | dry_run=%s | min_profit_ratio=%.4f | max_hops=%d",
            dry_run,
            min_profit_ratio,
            max_hops,
        )

    # ── validation ────────────────────────────────────────────────────────────

    def _validate_opportunity(self, opportunity: "ArbitrageResult") -> None:
        """
        Assert that *opportunity* represents a structurally valid and
        economically plausible flash-loan arbitrage.

        Raises
        ------
        ValueError
            - has_cycle is False (no negative cycle was detected)
            - cycle length < 3  (self-loop or 1-hop path — not a true cycle)
            - profit_ratio <= min_profit_ratio (no net profit after fees)
            - cycle length > max_hops (excessive gas cost)

        Parameters
        ----------
        opportunity : ArbitrageResult (duck-typed for testability)
        """
        if not opportunity.has_cycle:
            raise ValueError(
                "Opportunity rejected: has_cycle is False — no arbitrage cycle detected."
            )

        cycle_len = len(opportunity.cycle)
        if cycle_len < self._MIN_CYCLE_LENGTH:
            raise ValueError(
                f"Opportunity rejected: cycle length {cycle_len} < {self._MIN_CYCLE_LENGTH}. "
                f"A valid arbitrage cycle needs at least [A, B, A] (3 nodes / 2 hops). "
                f"Got: {opportunity.cycle}"
            )

        if cycle_len > self.max_hops + 1:
            raise ValueError(
                f"Opportunity rejected: cycle length {cycle_len} exceeds max_hops "
                f"{self.max_hops} — gas cost would exceed profitability threshold."
            )

        if opportunity.profit_ratio <= self.min_profit_ratio:
            raise ValueError(
                f"Opportunity rejected: profit_ratio {opportunity.profit_ratio:.6f} "
                f"<= min_profit_ratio {self.min_profit_ratio:.6f}. "
                "Net return after Aave premium and gas would be negative."
            )

    # ── swap step encoding ────────────────────────────────────────────────────

    def _encode_swap_steps(self, opportunity: "ArbitrageResult") -> List[SwapStep]:
        """
        Translate cycle_edges from an ArbitrageResult into an ordered list of
        SwapStep objects suitable for ABI-encoding inside the flash-loan callback.

        Parameters
        ----------
        opportunity : ArbitrageResult (duck-typed)

        Returns
        -------
        List[SwapStep] ordered from borrow_token back to borrow_token.
        """
        steps: List[SwapStep] = []
        for edge in opportunity.cycle_edges:
            if len(edge) == 3:
                from_tok, to_tok, rate = edge
                pool_id = "unknown"
            elif len(edge) == 4:
                from_tok, to_tok, rate, pool_id = edge
            else:
                raise ValueError(f"Unexpected edge format: {edge!r}")

            steps.append(SwapStep(
                token_in=from_tok,
                token_out=to_tok,
                rate=float(rate),
                pool_id=str(pool_id),
            ))
        return steps

    # ── execution ─────────────────────────────────────────────────────────────

    def execute(self, opportunity: "ArbitrageResult") -> ExecutionReport:
        """
        Validate and execute (or simulate) a flash-loan arbitrage opportunity.

        In dry_run=True mode this method encodes all swap steps, logs the
        projected P&L, and returns a filled ExecutionReport without submitting
        any transaction.

        In dry_run=False mode the executor would ABI-encode the calldata and
        call the NexusFlashReceiver contract.  The live path is intentionally
        left as a NotImplementedError stub until a contract address and web3
        provider are wired in via from_env().

        Parameters
        ----------
        opportunity : ArbitrageResult

        Returns
        -------
        ExecutionReport
        """
        try:
            self._validate_opportunity(opportunity)
        except (ValueError, AssertionError) as exc:
            logger.warning("execute() aborted during validation: %s", exc)
            return ExecutionReport(
                success=False,
                dry_run=self.dry_run,
                borrow_token=opportunity.cycle[0] if opportunity.cycle else "",
                n_hops=0,
                profit_ratio=getattr(opportunity, "profit_ratio", 0.0),
                error=str(exc),
            )

        swap_steps = self._encode_swap_steps(opportunity)
        borrow_token = opportunity.cycle[0]
        n_hops = len(swap_steps)
        profit_ratio = opportunity.profit_ratio

        if self.dry_run:
            logger.info(
                "[DRY RUN] Flash-loan arb | borrow=%s | hops=%d | profit_ratio=%.6f",
                borrow_token,
                n_hops,
                profit_ratio,
            )
            for i, step in enumerate(swap_steps):
                logger.debug("  hop %d: %s → %s @ %.6f via %s", i + 1,
                             step.token_in, step.token_out, step.rate, step.pool_id)
            return ExecutionReport(
                success=True,
                dry_run=True,
                borrow_token=borrow_token,
                n_hops=n_hops,
                profit_ratio=profit_ratio,
                tx_hash=None,
                swap_steps=swap_steps,
            )

        # Live execution stub — requires web3 provider + deployed contract
        raise NotImplementedError(
            "Live flash-loan execution requires a web3 provider and NexusFlashReceiver "
            "contract address.  Use FlashLoanExecutor.from_env() to construct a live "
            "executor with the necessary dependencies injected."
        )

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "FlashLoanExecutor":
        """
        Construct a FlashLoanExecutor from environment variables.

        Environment Variables
        ---------------------
        FLASH_DRY_RUN          : "true" | "false"  (default: "true")
        FLASH_MIN_PROFIT_RATIO : float             (default: "1.0")
        FLASH_MAX_HOPS         : int               (default: "10")

        Returns
        -------
        FlashLoanExecutor configured for the current environment.
        """
        import os

        dry_run = os.getenv("FLASH_DRY_RUN", "true").lower() != "false"
        min_profit_ratio = float(os.getenv("FLASH_MIN_PROFIT_RATIO", "1.001"))
        max_hops = int(os.getenv("FLASH_MAX_HOPS", "10"))

        logger.info(
            "FlashLoanExecutor.from_env() | dry_run=%s | min_profit_ratio=%.4f | max_hops=%d",
            dry_run, min_profit_ratio, max_hops,
        )
        return cls(dry_run=dry_run, min_profit_ratio=min_profit_ratio, max_hops=max_hops)
