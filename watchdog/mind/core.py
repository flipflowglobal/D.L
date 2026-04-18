"""
watchdog/mind/core.py — Central synchronization hub for the shared agent mind.

MindCore is the authoritative store for all agent shards.  Agents never
communicate directly; they push their shard to MindCore, which fan-outs
the update to any registered subscribers.

Architecture:
  - One shard per agent (keyed by agent_id)
  - Push-based sync: agent → MindCore → subscribers
  - Wildcard subscription (agent_id=None) receives every update
  - Global timeline: last MAX_TIMELINE entries across all agents
  - No network I/O — everything runs within a single asyncio event loop
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable, Coroutine, Dict, List, Optional

from watchdog.mind.shard import AgentShard

logger = logging.getLogger("watchdog.mind.core")

MAX_TIMELINE = 2000   # global event log capacity

# Handler type: async (agent_id, shard) -> None
SyncHandler = Callable[[str, AgentShard], Coroutine]


class MindCore:
    """
    Central node of the shared agent mind.

    All shard reads and writes are serialised through an asyncio.Lock to
    prevent data races when many agents push simultaneously.
    """

    def __init__(self) -> None:
        self._shards:      Dict[str, AgentShard]               = {}
        self._subscribers: Dict[Optional[str], List[SyncHandler]] = {}
        self._timeline:    deque                                = deque(maxlen=MAX_TIMELINE)
        self._sync_count:  int                                  = 0
        self._lock         = asyncio.Lock()

    # ── Shard management ──────────────────────────────────────────────────────

    def register(self, agent_id: str) -> AgentShard:
        """Create a fresh shard for *agent_id* and return it."""
        shard = AgentShard(agent_id)
        self._shards[agent_id] = shard
        logger.debug("MindCore: registered shard '%s'", agent_id)
        return shard

    def get_shard(self, agent_id: str) -> Optional[AgentShard]:
        return self._shards.get(agent_id)

    def all_shards(self) -> List[AgentShard]:
        """Return a snapshot list of all shards (safe to iterate)."""
        return list(self._shards.values())

    # ── Synchronization ───────────────────────────────────────────────────────

    async def sync(self, shard: AgentShard) -> None:
        """
        Accept an updated shard from its owning agent and propagate
        the change to all registered subscribers.

        Called by agents after every check().
        """
        async with self._lock:
            # Merge into the authoritative copy (last-write-wins)
            existing = self._shards.get(shard.agent_id)
            if existing is None:
                self._shards[shard.agent_id] = shard
            else:
                existing.merge(shard)

            self._sync_count += 1
            self._timeline.append({
                "agent_id":  shard.agent_id,
                "state":     shard.state,
                "clock":     shard.vector_clock,
                "wall_time": time.time(),
            })

        # Fan-out outside the lock to avoid deadlocks
        await self._notify(shard.agent_id, shard)

    async def _notify(self, agent_id: str, shard: AgentShard) -> None:
        """Deliver update to per-agent and wildcard subscribers."""
        for handler in self._subscribers.get(agent_id, []):
            try:
                await handler(agent_id, shard)
            except Exception as exc:
                logger.error("Sync handler error for '%s': %s", agent_id, exc)
        for handler in self._subscribers.get(None, []):
            try:
                await handler(agent_id, shard)
            except Exception as exc:
                logger.error("Wildcard sync handler error: %s", exc)

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe_shard(
        self,
        handler:   SyncHandler,
        agent_id:  Optional[str] = None,
    ) -> None:
        """
        Register *handler* to be called when *agent_id*'s shard is synced.
        Pass ``agent_id=None`` to receive ALL shard updates (wildcard).
        """
        self._subscribers.setdefault(agent_id, []).append(handler)
        logger.debug(
            "MindCore: subscribed handler '%s' (filter=%s)",
            handler.__name__, agent_id or "*",
        )

    # ── Broadcast ─────────────────────────────────────────────────────────────

    async def broadcast(
        self,
        from_agent: str,
        topic:      str,
        data:       dict,
    ) -> None:
        """
        Record a broadcast message on the global timeline.

        Subscribers can observe it via timeline() or by watching
        the broadcasting agent's shard.
        """
        async with self._lock:
            self._timeline.append({
                "from":      from_agent,
                "topic":     topic,
                "data":      data,
                "wall_time": time.time(),
            })
        logger.debug("Broadcast from '%s': topic=%s", from_agent, topic)

    # ── Query helpers ─────────────────────────────────────────────────────────

    def is_any_healing(self, exclude_agent: Optional[str] = None) -> bool:
        """Return True if any agent (except *exclude_agent*) is in 'healing' state."""
        for aid, shard in self._shards.items():
            if aid == exclude_agent:
                continue
            if shard.state == "healing":
                return True
        return False

    def agents_in_state(self, state: str) -> List[str]:
        """Return agent_ids whose current shard state equals *state*."""
        return [aid for aid, s in self._shards.items() if s.state == state]

    # ── Global snapshot ───────────────────────────────────────────────────────

    def global_snapshot(self) -> dict:
        """JSON-safe merged view of all shards."""
        state_counts: dict = {}
        for shard in self._shards.values():
            state_counts[shard.state] = state_counts.get(shard.state, 0) + 1

        # Determine overall mind health
        if state_counts.get("critical", 0) > 0:
            overall = "critical"
        elif state_counts.get("healing", 0) > 0:
            overall = "healing"
        elif state_counts.get("degraded", 0) > 0:
            overall = "degraded"
        elif state_counts.get("unknown", 0) == len(self._shards):
            overall = "unknown"
        else:
            overall = "healthy"

        return {
            "overall_state":  overall,
            "total_shards":   len(self._shards),
            "state_counts":   state_counts,
            "sync_count":     self._sync_count,
            "shards":         [s.to_dict() for s in self._shards.values()],
        }

    def timeline(self, n: int = 50) -> list:
        """Return the last *n* global timeline entries."""
        return list(self._timeline)[-n:]

    @property
    def stats(self) -> dict:
        return {
            "total_shards":   len(self._shards),
            "sync_count":     self._sync_count,
            "timeline_size":  len(self._timeline),
            "subscribers":    sum(len(v) for v in self._subscribers.values()),
        }


# ── Module singleton ──────────────────────────────────────────────────────────
mind_core = MindCore()
