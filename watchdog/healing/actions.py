"""
watchdog/healing/actions.py — Centralised self-healing strategy engine.

The HealingStrategy decides *whether* to trigger healing for a given event,
enforcing:
  - Per-agent cool-downs between successive heal attempts
  - Maximum heal attempts within a rolling window
  - Escalation policy (WARNING → CRITICAL → GIVE UP)

Agents perform the actual healing; this module only gate-keeps when to call them.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, Optional

from watchdog.event_bus import EventSeverity, EventType, WatchdogEvent

logger = logging.getLogger("watchdog.healing")


# ── Escalation levels ─────────────────────────────────────────────────────────

class HealEscalation(Enum):
    ATTEMPT   = auto()   # try to heal
    WAIT      = auto()   # in cool-down — skip
    GIVE_UP   = auto()   # exceeded max attempts


# ── Per-agent heal record ─────────────────────────────────────────────────────

@dataclass
class HealRecord:
    agent_id:      str
    total_heals:   int   = 0
    failed_heals:  int   = 0
    last_heal_at:  float = 0.0
    last_event:    Optional[EventType] = None

    def cooldown_remaining(self, cooldown: float) -> float:
        return max(0.0, cooldown - (time.monotonic() - self.last_heal_at))


# ── Healing action descriptor (returned to kernel) ────────────────────────────

@dataclass
class HealingAction:
    """Instruction returned by HealingStrategy.evaluate()."""
    escalation: HealEscalation
    agent_id:   str
    event:      WatchdogEvent
    attempt_no: int = 1
    reason:     str = ""

    @property
    def should_heal(self) -> bool:
        return self.escalation == HealEscalation.ATTEMPT


# ── Strategy engine ───────────────────────────────────────────────────────────

class HealingStrategy:
    """
    Gate-keeps self-healing calls.

    Parameters
    ----------
    cooldown_sec   : minimum seconds between successive heal attempts per agent
    max_attempts   : give up after this many total failed heals per agent
    window_sec     : rolling window for counting recent failures
    """

    def __init__(
        self,
        cooldown_sec: float = 30.0,
        max_attempts: int   = 10,
        window_sec:   float = 600.0,
    ) -> None:
        self.cooldown_sec = cooldown_sec
        self.max_attempts = max_attempts
        self.window_sec   = window_sec
        self._records: Dict[str, HealRecord] = {}
        self._recent_failures: Dict[str, list[float]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate(self, event: WatchdogEvent) -> HealingAction:
        """
        Given a CRITICAL event, decide whether healing should be attempted.

        Returns a HealingAction whose `.should_heal` indicates the decision.
        """
        if event.severity != EventSeverity.CRITICAL:
            # Only self-heal on critical events
            return HealingAction(
                escalation = HealEscalation.WAIT,
                agent_id   = event.agent_id,
                event      = event,
                reason     = "Non-critical severity — no healing needed",
            )

        rec = self._get_or_create(event.agent_id)

        # ── Check cool-down ───────────────────────────────────────────────────
        remaining = rec.cooldown_remaining(self.cooldown_sec)
        if remaining > 0:
            return HealingAction(
                escalation = HealEscalation.WAIT,
                agent_id   = event.agent_id,
                event      = event,
                reason     = f"Cool-down: {remaining:.1f}s remaining",
            )

        # ── Check rolling failure window ──────────────────────────────────────
        recent = self._prune_recent(event.agent_id)
        if len(recent) >= self.max_attempts:
            return HealingAction(
                escalation = HealEscalation.GIVE_UP,
                agent_id   = event.agent_id,
                event      = event,
                attempt_no = rec.total_heals + 1,
                reason     = (
                    f"Exceeded {self.max_attempts} heal attempts "
                    f"in {self.window_sec:.0f}s window"
                ),
            )

        attempt_no = rec.total_heals + 1
        return HealingAction(
            escalation = HealEscalation.ATTEMPT,
            agent_id   = event.agent_id,
            event      = event,
            attempt_no = attempt_no,
            reason     = f"Heal attempt #{attempt_no}",
        )

    def record_result(self, agent_id: str, success: bool) -> None:
        """Call after a healing attempt to update internal state."""
        rec = self._get_or_create(agent_id)
        rec.total_heals  += 1
        rec.last_heal_at  = time.monotonic()
        if not success:
            rec.failed_heals += 1
            self._recent_failures.setdefault(agent_id, []).append(time.monotonic())
        else:
            # Reset failure window on success
            self._recent_failures[agent_id] = []
        logger.debug(
            "Heal record updated: agent=%s success=%s total=%d failed=%d",
            agent_id, success, rec.total_heals, rec.failed_heals,
        )

    def get_record(self, agent_id: str) -> Optional[HealRecord]:
        return self._records.get(agent_id)

    def snapshot(self) -> list[dict]:
        """Return a list of dicts summarising all heal records."""
        return [
            {
                "agent_id":     r.agent_id,
                "total_heals":  r.total_heals,
                "failed_heals": r.failed_heals,
                "last_heal_ago_s": round(time.monotonic() - r.last_heal_at, 1)
                    if r.last_heal_at else None,
            }
            for r in self._records.values()
        ]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_or_create(self, agent_id: str) -> HealRecord:
        if agent_id not in self._records:
            self._records[agent_id] = HealRecord(agent_id=agent_id)
        return self._records[agent_id]

    def _prune_recent(self, agent_id: str) -> list[float]:
        """Remove timestamps older than window_sec and return the pruned list."""
        cutoff = time.monotonic() - self.window_sec
        failures = self._recent_failures.get(agent_id, [])
        fresh = [t for t in failures if t > cutoff]
        self._recent_failures[agent_id] = fresh
        return fresh


# ── Module-level singleton ────────────────────────────────────────────────────
healing_strategy = HealingStrategy()
