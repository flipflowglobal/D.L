"""
watchdog/mind/sync.py — SyncBridge and SharedMind façade.

SyncBridge
----------
A per-agent handle to the shared mind.  Each WatchdogAgent receives one
SyncBridge (assigned by the kernel after startup).  The bridge provides
a clean, agent-scoped API:

  await bridge.push("healthy", severity="INFO", cycle=42)
  await bridge.set_healing(True)
  peer = bridge.query_peer("db:aureon_memory.db")
  bridge.watch_peer("trade:aureon-loop", my_callback)
  await bridge.broadcast("cycle_complete", cycle=42)

SharedMind
----------
Top-level façade used by the kernel.  Wraps MindCore + ConsensusEngine
and exposes:

  bridge = mind.connect(agent_id)          # give each agent its bridge
  result = await mind.propose_heal(event)  # consensus before healing
  snap   = mind.global_snapshot()          # dashboard data
  tl     = mind.timeline(n=100)            # ordered event log
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Coroutine, List, Optional

from watchdog.mind.core      import MindCore, mind_core
from watchdog.mind.consensus import ConsensusEngine, ConsensusResult, consensus_engine
from watchdog.mind.shard     import AgentShard

if TYPE_CHECKING:
    from watchdog.event_bus import WatchdogEvent

logger = logging.getLogger("watchdog.mind.sync")


# ── Per-agent bridge ──────────────────────────────────────────────────────────

class SyncBridge:
    """
    Agent-scoped interface to the shared mind.

    Created via ``SharedMind.connect(agent_id)`` — agents never
    instantiate this directly.
    """

    def __init__(
        self,
        agent_id: str,
        shard:    AgentShard,
        core:     MindCore,
    ) -> None:
        self.agent_id = agent_id
        self.shard    = shard
        self._core    = core

    # ── Push: agent writes to mind ────────────────────────────────────────────

    async def push(self, state: str, **observations: Any) -> None:
        """
        Push the agent's current state + observations to MindCore.

        Call this after every check() to keep the shared mind in sync.

        Args:
            state: "healthy" | "degraded" | "critical" | "healing" | "unknown"
            **observations: arbitrary key-value data written to the shard
        """
        self.shard.state = state
        for key, value in observations.items():
            self.shard.observe(key, value)
        await self._core.sync(self.shard)

    async def set_healing(self, active: bool = True) -> None:
        """
        Declare that this agent is entering (or leaving) a healing state.

        Sets shard state to "healing" / "unknown" and propagates immediately
        so peers can see the conflict guard in the consensus engine.
        """
        state = "healing" if active else "unknown"
        await self.push(state)

    # ── Query: agent reads peers ──────────────────────────────────────────────

    def query_peer(self, peer_id: str) -> Optional[AgentShard]:
        """Read the latest shard for *peer_id* from MindCore (synchronous)."""
        return self._core.get_shard(peer_id)

    def query_all_peers(self) -> List[AgentShard]:
        """Return all shards except this agent's own shard."""
        return [s for s in self._core.all_shards() if s.agent_id != self.agent_id]

    def is_system_healing(self) -> bool:
        """Return True if any other agent is currently in 'healing' state."""
        return self._core.is_any_healing(exclude_agent=self.agent_id)

    def agents_in_state(self, state: str) -> List[str]:
        """Return agent_ids (excluding self) whose shard state equals *state*."""
        return [
            aid for aid in self._core.agents_in_state(state)
            if aid != self.agent_id
        ]

    # ── Watch: receive peer shard updates ────────────────────────────────────

    def watch_peer(
        self,
        peer_id:  str,
        callback: Callable[[str, AgentShard], Coroutine],
    ) -> None:
        """
        Register an async callback to be called whenever *peer_id* syncs its shard.

        callback signature: async def on_update(agent_id: str, shard: AgentShard)
        """
        self._core.subscribe_shard(callback, agent_id=peer_id)
        logger.debug("'%s' is now watching peer '%s'", self.agent_id, peer_id)

    def watch_all(
        self,
        callback: Callable[[str, AgentShard], Coroutine],
    ) -> None:
        """Subscribe to ALL shard updates across every agent."""
        self._core.subscribe_shard(callback, agent_id=None)

    # ── Broadcast messaging ───────────────────────────────────────────────────

    async def broadcast(self, topic: str, **data: Any) -> None:
        """
        Publish a named message to the global timeline.

        Other agents can observe it via mind.timeline() or by subscribing
        to this agent's shard updates.
        """
        await self._core.broadcast(
            from_agent = self.agent_id,
            topic      = topic,
            data       = data,
        )

    def __repr__(self) -> str:
        return f"SyncBridge(agent={self.agent_id!r}, state={self.shard.state!r})"


# ── SharedMind façade ─────────────────────────────────────────────────────────

class SharedMind:
    """
    Top-level façade for the shared agent mind system.

    The kernel holds one SharedMind instance and uses it to:
      1. Connect each agent (giving it a SyncBridge)
      2. Run consensus before executing heals
      3. Expose global state to the dashboard
    """

    def __init__(
        self,
        core:       MindCore       = mind_core,
        consensus:  ConsensusEngine = consensus_engine,
    ) -> None:
        self._core      = core
        self._consensus = consensus

    # ── Agent connection ──────────────────────────────────────────────────────

    def connect(self, agent_id: str) -> SyncBridge:
        """
        Register *agent_id* with the shared mind and return its SyncBridge.

        Called by the kernel during startup for every registered agent.
        """
        shard  = self._core.register(agent_id)
        bridge = SyncBridge(agent_id=agent_id, shard=shard, core=self._core)
        logger.debug("Agent '%s' connected to SharedMind", agent_id)
        return bridge

    # ── Consensus ─────────────────────────────────────────────────────────────

    async def propose_heal(self, event: "WatchdogEvent") -> ConsensusResult:
        """
        Run a consensus round for the given CRITICAL event.

        The kernel calls this before invoking agent.heal().
        Returns ConsensusResult — check ``.approved`` for the decision.
        """
        shards = self._core.all_shards()
        return await self._consensus.propose(event, shards)

    # ── Global view ───────────────────────────────────────────────────────────

    def global_snapshot(self) -> dict:
        """JSON-safe merged view of all shards plus consensus stats."""
        snap = self._core.global_snapshot()
        snap["consensus_stats"] = self._consensus.stats
        snap["mind_stats"]      = self._core.stats
        return snap

    def timeline(self, n: int = 50) -> list:
        """Return the last *n* entries from the global timeline."""
        return self._core.timeline(n)

    def get_shard(self, agent_id: str) -> Optional[AgentShard]:
        """Direct shard lookup — used by the dashboard."""
        return self._core.get_shard(agent_id)


# ── Module singleton ──────────────────────────────────────────────────────────
shared_mind = SharedMind()
