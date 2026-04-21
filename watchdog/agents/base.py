"""
watchdog/agents/base.py — Abstract base class for all watchdog agents.

Every agent runs as an independent asyncio Task, polls its assigned
resource on a configurable interval, and publishes WatchdogEvents to
the shared EventBus.

Shared-mind integration
-----------------------
Each agent holds an optional ``SyncBridge`` (set by the kernel via
``agent.mind = shared_mind.connect(agent_id)`` after registration).

When the bridge is present:
  - After every check(), the agent's state is pushed to MindCore so
    peers can read it.
  - During healing, the agent marks itself as "healing" so the
    consensus engine can prevent concurrent heals on the same subsystem.
  - After healing completes, the state reverts to "healthy"/"unknown".
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from watchdog.event_bus import EventBus, EventSeverity, EventType, WatchdogEvent

if TYPE_CHECKING:
    from watchdog.mind.sync import SyncBridge

logger = logging.getLogger("watchdog.agent.base")

# Map event severity to shard state string
_SEVERITY_TO_STATE = {
    EventSeverity.INFO:     "healthy",
    EventSeverity.WARNING:  "degraded",
    EventSeverity.CRITICAL: "critical",
    EventSeverity.HEALED:   "healthy",
}


class WatchdogAgent(ABC):
    """
    Abstract base for all watchdog agents.

    Each agent:
      - Has a unique ``agent_id`` (derived from the resource it watches).
      - Polls on a configurable ``interval`` (seconds).
      - Publishes events to the shared ``EventBus``.
      - Optionally participates in the shared mind via a ``SyncBridge``.
    """

    def __init__(
        self,
        agent_id:   str,
        source:     str,
        bus:        EventBus,
        interval:   float = 10.0,
    ) -> None:
        self.agent_id  = agent_id
        self.source    = source
        self.bus       = bus
        self.interval  = interval

        # Shared-mind bridge — injected by kernel after registration
        self.mind: Optional["SyncBridge"] = None

        self._running:       bool              = False
        self._task:          Optional[asyncio.Task] = None
        self._failures:      int               = 0
        self._last_ok:       float             = time.monotonic()
        self._check_count:   int               = 0
        self._started_at:    float             = 0.0

        self.log = logging.getLogger(f"watchdog.agent.{agent_id}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch the polling loop as an asyncio Task."""
        self._running    = True
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(
            self._poll_loop(), name=f"wdog-{self.agent_id}"
        )
        self.log.debug("Started (interval=%.1fs)", self.interval)

    async def stop(self) -> None:
        """Cancel the polling task and wait for it to finish."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.log.debug("Stopped after %d checks", self._check_count)

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def check(self) -> WatchdogEvent:
        """
        Perform one health check and return the resulting event.

        Must never raise — catch all exceptions internally and
        return an appropriate CRITICAL event instead.
        """
        ...

    @abstractmethod
    async def heal(self, event: WatchdogEvent) -> bool:
        """
        Attempt to self-heal the failure described by *event*.

        Returns:
            True if healing succeeded, False otherwise.
        """
        ...

    # ── Poll loop ─────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Repeatedly call check(), publish the result, heal on CRITICAL."""
        while self._running:
            self._check_count += 1
            try:
                event = await self.check()
            except Exception as exc:
                event = self._make_event(
                    EventType.FILE_MISSING,
                    EventSeverity.CRITICAL,
                    f"check() raised unexpectedly: {exc}",
                )

            await self.bus.publish(event)

            # ── Sync to shared mind ───────────────────────────────────────────
            await self._sync_to_mind(event)

            if event.severity == EventSeverity.CRITICAL:
                self._failures += 1
                healed = await self._try_heal(event)
                if not healed:
                    self.log.error(
                        "Healing FAILED for %s (failure #%d)", self.source, self._failures
                    )
            elif event.severity == EventSeverity.INFO:
                if self._failures > 0:
                    self.log.info("Recovered: %s (was %d failures)", self.source, self._failures)
                self._failures = 0
                self._last_ok  = time.monotonic()

            await asyncio.sleep(self.interval)

    async def _try_heal(self, event: WatchdogEvent) -> bool:
        """
        Publish HEALING_STARTED, declare healing state to the mind,
        call heal(), restore state, publish outcome.
        """
        await self.bus.publish(self._make_event(
            EventType.HEALING_STARTED,
            EventSeverity.INFO,
            f"Healing attempt #{self._failures} for {event.event_type.name}",
        ))

        # Tell peers we are healing
        if self.mind:
            await self.mind.set_healing(True)

        try:
            success = await self.heal(event)
        except Exception as exc:
            self.log.error("heal() raised: %s", exc)
            success = False

        # Restore mind state
        if self.mind:
            post_state = "healthy" if success else "critical"
            await self.mind.push(post_state, last_heal_success=success)

        outcome_type = EventType.HEALING_SUCCESS if success else EventType.HEALING_FAILED
        await self.bus.publish(self._make_event(
            outcome_type,
            EventSeverity.HEALED if success else EventSeverity.CRITICAL,
            f"Healing {'succeeded' if success else 'failed'} for {self.source}",
        ))
        return success

    # ── Shared-mind sync ──────────────────────────────────────────────────────

    async def _sync_to_mind(self, event: WatchdogEvent) -> None:
        """Push this check's result to MindCore (no-op if mind not wired)."""
        if self.mind is None:
            return
        state = _SEVERITY_TO_STATE.get(event.severity, "unknown")
        try:
            await self.mind.push(
                state,
                event_type = event.event_type.name,
                message    = event.message[:120],   # truncate for shard storage
                check_no   = self._check_count,
                failures   = self._failures,
            )
        except Exception as exc:
            # Never let mind sync crash the poll loop
            self.log.warning("Mind sync failed: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_event(
        self,
        event_type: EventType,
        severity:   EventSeverity,
        message:    str,
        details:    dict | None = None,
    ) -> WatchdogEvent:
        return WatchdogEvent(
            event_type = event_type,
            severity   = severity,
            agent_id   = self.agent_id,
            source     = self.source,
            message    = message,
            details    = details or {},
        )

    @property
    def status(self) -> dict:
        """Snapshot of this agent's current health state."""
        snap: dict = {
            "agent_id":        self.agent_id,
            "source":          self.source,
            "running":         self._running,
            "failures":        self._failures,
            "checks":          self._check_count,
            "last_ok_ago_s":   round(time.monotonic() - self._last_ok, 1),
            "uptime_s":        round(time.monotonic() - self._started_at, 1),
            "mind_connected":  self.mind is not None,
        }
        if self.mind:
            snap["shard_state"]  = self.mind.shard.state
            snap["vector_clock"] = self.mind.shard.vector_clock
        return snap
