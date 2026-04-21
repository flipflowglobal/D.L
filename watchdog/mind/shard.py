"""
watchdog/mind/shard.py — Per-agent memory shard.

Each agent owns one AgentShard — a named slice of the global shared mind.
Shards store:
  - Named observations (rolling history, last MAX_OBSERVATIONS entries)
  - Current derived state ("healthy" | "degraded" | "critical" | "healing" | "unknown")
  - A vector clock for deterministic conflict resolution when merging
    shards across sync boundaries

Shards are always local (in-process); MindCore is the authority
that stores and propagates them.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque

MAX_OBSERVATIONS = 64   # rolling history per shard

# Valid state labels
AgentState = str   # "healthy" | "degraded" | "critical" | "healing" | "unknown"


@dataclass
class Observation:
    """A single timestamped key-value entry recorded by an agent."""
    key:       str
    value:     Any
    timestamp: float = field(default_factory=time.time)
    clock:     int   = 0

    def to_dict(self) -> dict:
        return {
            "key":       self.key,
            "value":     self.value,
            "timestamp": round(self.timestamp, 3),
            "clock":     self.clock,
        }


class AgentShard:
    """
    A named slice of the shared mind, owned by one agent.

    All writes go through ``observe()``; MindCore.sync() must be
    called afterward to propagate the change to peers.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id:     str                   = agent_id
        self.state:        AgentState            = "unknown"
        self.vector_clock: int                   = 0
        self.updated_at:   float                 = time.monotonic()
        self._obs:         Deque[Observation]    = deque(maxlen=MAX_OBSERVATIONS)
        self._current:     dict                  = {}   # latest value per key

    # ── Write ─────────────────────────────────────────────────────────────────

    def observe(self, key: str, value: Any) -> None:
        """Record *key=value* and increment the vector clock."""
        self.vector_clock += 1
        self.updated_at    = time.monotonic()
        obs = Observation(key=key, value=value, clock=self.vector_clock)
        self._obs.append(obs)
        self._current[key] = value

    def set_state(self, state: AgentState) -> None:
        """Update agent state without creating an observation entry."""
        self.state      = state
        self.updated_at = time.monotonic()

    # ── Read ──────────────────────────────────────────────────────────────────

    def read(self, key: str, default: Any = None) -> Any:
        """Return the most recent value for *key*, or *default*."""
        return self._current.get(key, default)

    def recent(self, n: int = 10) -> list[dict]:
        """Return the last *n* observations as JSON-safe dicts."""
        return [o.to_dict() for o in list(self._obs)[-n:]]

    def age_seconds(self) -> float:
        """Seconds since this shard was last updated."""
        return time.monotonic() - self.updated_at

    # ── Merge (last-write-wins by vector clock) ───────────────────────────────

    def merge(self, remote: "AgentShard") -> bool:
        """
        Merge state from *remote* into this shard.

        Uses vector clock for conflict resolution — the higher clock wins.
        Returns True if this shard was updated.
        """
        if remote.vector_clock <= self.vector_clock:
            return False    # our copy is at least as fresh
        self.state        = remote.state
        self.vector_clock = remote.vector_clock
        self.updated_at   = remote.updated_at
        self._current.update(remote._current)
        # Import last 5 remote observations (avoid duplicating long history)
        for obs in list(remote._obs)[-5:]:
            self._obs.append(obs)
        return True

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "agent_id":      self.agent_id,
            "state":         self.state,
            "vector_clock":  self.vector_clock,
            "age_s":         round(self.age_seconds(), 2),
            "current":       dict(self._current),
            "recent_obs":    self.recent(5),
        }

    def __repr__(self) -> str:
        return f"AgentShard(id={self.agent_id!r}, state={self.state!r}, clock={self.vector_clock})"
