"""
watchdog/mind/consensus.py — Multi-agent consensus for healing proposals.

Before the kernel executes a heal, ConsensusEngine evaluates peer shard
states and returns an approved/rejected decision.

Voting rules (implicit — based on peer shard state):
  ┌────────────────┬───────────────────────────────────────────────────────┐
  │ Peer state     │ Vote                                                   │
  ├────────────────┼───────────────────────────────────────────────────────┤
  │ healing        │ NO  if same subsystem prefix, else ABSTAIN             │
  │ critical       │ YES (corroborating evidence — system-wide issue)       │
  │ degraded       │ YES (supports the heal)                                │
  │ healthy        │ YES (isolated failure — safe to heal)                  │
  │ unknown        │ ABSTAIN                                                │
  └────────────────┴───────────────────────────────────────────────────────┘

Quorum rule:
  - YES votes ÷ (YES + NO votes) ≥ QUORUM_RATIO  →  APPROVED
  - Tie-break  →  APPROVED  (fail-open keeps the system self-healing)
  - No active votes (all ABSTAIN or no peers)  →  APPROVED (solo approval)

Conflict guard (runs before voting):
  - If any peer in the *same subsystem* is actively healing  →  REJECTED
    immediately (prevents concurrent heals on the same component).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional

from watchdog.event_bus  import WatchdogEvent
from watchdog.mind.shard import AgentShard

logger = logging.getLogger("watchdog.mind.consensus")

QUORUM_RATIO = 0.51   # fraction of active (YES + NO) votes required


class Vote(Enum):
    YES     = auto()
    NO      = auto()
    ABSTAIN = auto()


@dataclass
class HealProposal:
    """A heal request submitted to the consensus engine."""
    proposer:   str            # agent_id requesting the heal
    event:      WatchdogEvent
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class ConsensusResult:
    """Outcome of a consensus round."""
    proposal:   HealProposal
    approved:   bool
    votes:      Dict[str, Vote]   # peer agent_id → Vote
    reason:     str  = ""
    elapsed_ms: float = 0.0

    @property
    def yes_count(self) -> int:
        return sum(1 for v in self.votes.values() if v == Vote.YES)

    @property
    def no_count(self) -> int:
        return sum(1 for v in self.votes.values() if v == Vote.NO)

    @property
    def abstain_count(self) -> int:
        return sum(1 for v in self.votes.values() if v == Vote.ABSTAIN)

    def to_dict(self) -> dict:
        return {
            "approved":   self.approved,
            "proposer":   self.proposal.proposer,
            "reason":     self.reason,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "votes": {
                aid: v.name for aid, v in self.votes.items()
            },
            "tally": {
                "yes":     self.yes_count,
                "no":      self.no_count,
                "abstain": self.abstain_count,
            },
        }


class ConsensusEngine:
    """
    Evaluates peer shard states to decide whether to approve a heal proposal.

    All decisions are synchronous (no blocking waits) — votes are derived
    from the current state of each peer's shard at call time.
    """

    def __init__(self, quorum_ratio: float = QUORUM_RATIO) -> None:
        self.quorum_ratio  = quorum_ratio
        self._total_rounds = 0
        self._approved     = 0
        self._rejected     = 0

    async def propose(
        self,
        event:      WatchdogEvent,
        all_shards: List[AgentShard],
    ) -> ConsensusResult:
        """
        Run a consensus round for *event*.

        Args:
            event:      The CRITICAL event triggering the heal proposal.
            all_shards: Current snapshot of all agent shards from MindCore.

        Returns:
            ConsensusResult — check ``.approved`` for the decision.
        """
        t0 = time.monotonic()
        proposal = HealProposal(proposer=event.agent_id, event=event)
        peers = [s for s in all_shards if s.agent_id != event.agent_id]

        # ── Conflict guard ────────────────────────────────────────────────────
        for peer in peers:
            if peer.state == "healing" and self._same_subsystem(peer.agent_id, event.agent_id):
                result = ConsensusResult(
                    proposal   = proposal,
                    approved   = False,
                    votes      = {peer.agent_id: Vote.NO},
                    reason     = (
                        f"Conflict: {peer.agent_id} is already healing "
                        f"the same subsystem"
                    ),
                    elapsed_ms = (time.monotonic() - t0) * 1000,
                )
                self._total_rounds += 1
                self._rejected += 1
                self._log(result)
                return result

        # ── Implicit voting ───────────────────────────────────────────────────
        votes: Dict[str, Vote] = {}
        for peer in peers:
            votes[peer.agent_id] = self._cast_vote(peer, event)

        approved, reason = self._evaluate(votes)
        elapsed_ms = (time.monotonic() - t0) * 1000

        result = ConsensusResult(
            proposal   = proposal,
            approved   = approved,
            votes      = votes,
            reason     = reason,
            elapsed_ms = elapsed_ms,
        )
        self._total_rounds += 1
        if approved:
            self._approved += 1
        else:
            self._rejected += 1
        self._log(result)
        return result

    # ── Vote casting ──────────────────────────────────────────────────────────

    def _cast_vote(self, peer: AgentShard, event: WatchdogEvent) -> Vote:
        state = peer.state

        if state == "healing":
            # Different subsystem currently healing — abstain (not our concern)
            return Vote.ABSTAIN

        if state == "critical":
            # Another agent also in crisis — corroborate the heal
            return Vote.YES

        if state in ("degraded", "healthy"):
            # Peer is functional — supports the requesting agent being healed
            return Vote.YES

        # "unknown" or anything else
        return Vote.ABSTAIN

    # ── Quorum evaluation ─────────────────────────────────────────────────────

    def _evaluate(self, votes: Dict[str, Vote]) -> tuple[bool, str]:
        yes    = sum(1 for v in votes.values() if v == Vote.YES)
        no     = sum(1 for v in votes.values() if v == Vote.NO)
        active = yes + no   # abstains don't count toward quorum

        if active == 0:
            return True, "Solo approval — no peer votes"

        ratio = yes / active
        if ratio >= self.quorum_ratio:
            return True, f"Quorum reached: {yes}/{active} YES ({ratio:.0%})"
        if no > yes:
            return False, f"Quorum failed: {yes}/{active} YES ({ratio:.0%})"
        # Tie → approve (fail-open)
        return True, f"Tie-break approval: {yes}/{active} YES"

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _same_subsystem(agent_a: str, agent_b: str) -> bool:
        """
        Two agents share a subsystem if their agent_id prefixes match.
        E.g. "service:aureon-api" and "service:aureon-server" → True.
        """
        def prefix(aid: str) -> str:
            return aid.split(":")[0] if ":" in aid else aid
        return prefix(agent_a) == prefix(agent_b)

    def _log(self, result: ConsensusResult) -> None:
        verdict = "APPROVED" if result.approved else "REJECTED"
        logger.debug(
            "Consensus %s for '%s' — %s (YES=%d NO=%d ABSTAIN=%d, %.1fms)",
            verdict,
            result.proposal.proposer,
            result.reason,
            result.yes_count,
            result.no_count,
            result.abstain_count,
            result.elapsed_ms,
        )

    @property
    def stats(self) -> dict:
        return {
            "total_rounds": self._total_rounds,
            "approved":     self._approved,
            "rejected":     self._rejected,
        }


# ── Module singleton ──────────────────────────────────────────────────────────
consensus_engine = ConsensusEngine()
