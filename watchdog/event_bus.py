"""
watchdog/event_bus.py — Typed async event bus for the watchdog kernel.

All agents publish WatchdogEvent objects onto the bus.
The kernel subscribes and routes events to the self-healing engine.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("watchdog.event_bus")


class EventSeverity(Enum):
    INFO     = auto()   # normal operation
    WARNING  = auto()   # degraded but functional
    CRITICAL = auto()   # requires immediate healing
    HEALED   = auto()   # recovery confirmed


class EventType(Enum):
    # ── File events ───────────────────────────────────────────────────────────
    FILE_OK            = auto()
    FILE_MODIFIED      = auto()   # unexpected hash change
    FILE_MISSING       = auto()   # file was deleted
    FILE_SYNTAX_ERROR  = auto()   # ast.parse() failed
    FILE_IMPORT_ERROR  = auto()   # importlib load failed

    # ── Process events (Rust sidecars) ────────────────────────────────────────
    PROCESS_OK         = auto()
    PROCESS_DEAD       = auto()
    PROCESS_UNHEALTHY  = auto()   # running but /health returns non-200
    PROCESS_RECOVERED  = auto()

    # ── HTTP service events ───────────────────────────────────────────────────
    SERVICE_OK         = auto()
    SERVICE_DOWN       = auto()
    SERVICE_DEGRADED   = auto()
    SERVICE_RECOVERED  = auto()

    # ── Trading loop events ───────────────────────────────────────────────────
    TRADE_LOOP_OK      = auto()
    TRADE_LOOP_STALE   = auto()   # no cycle update within threshold
    TRADE_LOOP_DEAD    = auto()   # loop.running == False unexpectedly
    TRADE_LOOP_HEALED  = auto()

    # ── Database events ───────────────────────────────────────────────────────
    DB_OK              = auto()
    DB_MISSING         = auto()
    DB_CORRUPT         = auto()
    DB_HEALED          = auto()

    # ── Resource events ───────────────────────────────────────────────────────
    RESOURCE_OK           = auto()
    RESOURCE_CPU_HIGH     = auto()
    RESOURCE_MEM_HIGH     = auto()
    RESOURCE_DISK_CRITICAL = auto()
    RESOURCE_FD_HIGH      = auto()

    # ── Healing events ────────────────────────────────────────────────────────
    HEALING_STARTED    = auto()
    HEALING_SUCCESS    = auto()
    HEALING_FAILED     = auto()


@dataclass
class WatchdogEvent:
    """A single event emitted by any watchdog agent."""
    event_type: EventType
    severity:   EventSeverity
    agent_id:   str               # which agent fired this
    source:     str               # file path, process name, service name, etc.
    message:    str
    timestamp:  float = field(default_factory=time.monotonic)
    wall_time:  float = field(default_factory=time.time)
    details:    dict  = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"[{self.severity.name:<8}] {self.event_type.name:<24} "
            f"agent={self.agent_id:<20} src={self.source}  {self.message}"
        )


# ── Subscriber type ───────────────────────────────────────────────────────────

EventHandler = Callable[[WatchdogEvent], Coroutine]


class EventBus:
    """
    Process-local async publish/subscribe event bus.

    Agents call publish() to emit events.
    The kernel subscribes handlers per EventType or for ALL events.
    """

    def __init__(self, maxsize: int = 2048) -> None:
        self._queue: asyncio.Queue[WatchdogEvent] = asyncio.Queue(maxsize=maxsize)
        self._handlers: list[tuple[Optional[EventType], EventHandler]] = []
        self._running  = False
        self._task: Optional[asyncio.Task] = None
        self._event_count = 0

    def subscribe(
        self,
        handler: EventHandler,
        event_type: Optional[EventType] = None,
    ) -> None:
        """
        Register *handler* coroutine for *event_type* (or ALL if None).

        Args:
            handler:    async callable accepting a WatchdogEvent.
            event_type: filter to this type only, or None for all events.
        """
        self._handlers.append((event_type, handler))
        logger.debug("Subscribed handler %s (filter=%s)", handler.__name__, event_type)

    async def publish(self, event: WatchdogEvent) -> None:
        """
        Enqueue an event.  Drops oldest if bus is full (non-blocking).
        """
        try:
            self._queue.put_nowait(event)
            self._event_count += 1
        except asyncio.QueueFull:
            logger.warning("EventBus full — dropping oldest event")
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            await self._queue.put(event)

    async def start(self) -> None:
        """Start the dispatch loop."""
        self._running = True
        self._task    = asyncio.create_task(self._dispatch_loop(), name="event-bus")
        logger.info("EventBus started (capacity=%d)", self._queue.maxsize)

    async def stop(self) -> None:
        """Drain remaining events and stop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("EventBus stopped (total events dispatched: %d)", self._event_count)

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            for filter_type, handler in self._handlers:
                if filter_type is None or filter_type == event.event_type:
                    try:
                        await handler(event)
                    except Exception as exc:
                        logger.error(
                            "Handler %s raised on %s: %s",
                            handler.__name__, event.event_type, exc,
                        )

    @property
    def stats(self) -> dict:
        return {
            "events_dispatched": self._event_count,
            "queue_size":        self._queue.qsize(),
            "handlers":          len(self._handlers),
        }


# ── Module-level singleton ────────────────────────────────────────────────────
event_bus = EventBus()
