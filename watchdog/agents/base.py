"""
watchdog/agents/base.py — Abstract base class for all watchdog agents.

Every agent runs as an independent asyncio Task, polls its assigned
resource on a configurable interval, and publishes WatchdogEvents to
the shared EventBus.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from watchdog.event_bus import EventBus, EventSeverity, EventType, WatchdogEvent

logger = logging.getLogger("watchdog.agent.base")


class WatchdogAgent(ABC):
    """
    Abstract base for all watchdog agents.

    Each agent:
      - Has a unique ``agent_id`` (typically derived from the resource it watches).
      - Polls on a configurable ``interval`` (seconds).
      - Publishes events to the shared ``EventBus``.
      - Tracks consecutive failure count for escalation logic.
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
        """Publish HEALING_STARTED, call heal(), publish outcome."""
        await self.bus.publish(self._make_event(
            EventType.HEALING_STARTED,
            EventSeverity.INFO,
            f"Healing attempt #{self._failures} for {event.event_type.name}",
        ))
        try:
            success = await self.heal(event)
        except Exception as exc:
            self.log.error("heal() raised: %s", exc)
            success = False

        outcome_type = EventType.HEALING_SUCCESS if success else EventType.HEALING_FAILED
        await self.bus.publish(self._make_event(
            outcome_type,
            EventSeverity.HEALED if success else EventSeverity.CRITICAL,
            f"Healing {'succeeded' if success else 'failed'} for {self.source}",
        ))
        return success

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
        return {
            "agent_id":    self.agent_id,
            "source":      self.source,
            "running":     self._running,
            "failures":    self._failures,
            "checks":      self._check_count,
            "last_ok_ago": round(time.monotonic() - self._last_ok, 1),
            "uptime_s":    round(time.monotonic() - self._started_at, 1),
        }
