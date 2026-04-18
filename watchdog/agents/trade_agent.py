"""
watchdog/agents/trade_agent.py — Autonomous trading loop watchdog.

Monitors the AgentLoop singleton (intelligence/autonomy.py) by:
  1. Tracking whether loop.running is True when it should be
  2. Detecting stale cycles — no last_run update within STALE_THRESHOLD
  3. Reading last_result from SQLite memory for error patterns

Self-healing:
  - Loop dead (running=False unexpectedly) → re-invoke loop.run()
  - Stale cycle → log CRITICAL, attempt restart
  - Error pattern in last_result → escalate WARNING
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from watchdog.agents.base import WatchdogAgent
from watchdog.event_bus import EventBus, EventSeverity, EventType, WatchdogEvent

logger = logging.getLogger("watchdog.agent.trade")

STALE_THRESHOLD = 120   # seconds without a cycle update before STALE event
LOOP_AGENT_ID   = "AUREON"


class TradeLoopAgent(WatchdogAgent):
    """
    Watchdog for the autonomous trading loop.

    Requires access to the `loop` singleton from intelligence.autonomy
    and the `memory` singleton from intelligence.memory.
    """

    def __init__(
        self,
        bus:            EventBus,
        interval:       float = 20.0,
        auto_heal:      bool  = True,
    ) -> None:
        super().__init__(
            agent_id = "trade:aureon-loop",
            source   = "intelligence.autonomy.loop",
            bus      = bus,
            interval = interval,
        )
        self.auto_heal    = auto_heal
        self._loop_ref    = None    # lazy: resolved on first check
        self._memory_ref  = None
        self._expected_running: bool = False    # tracks intended state
        self._last_cycle: Optional[int]  = None
        self._last_cycle_time: float     = time.monotonic()

    # ── Lazy resolution of live objects ──────────────────────────────────────

    def _resolve(self) -> bool:
        """Import loop/memory singletons. Returns True if available."""
        if self._loop_ref is None:
            try:
                from intelligence.autonomy import loop as _loop
                self._loop_ref = _loop
            except Exception:
                return False
        if self._memory_ref is None:
            try:
                from intelligence.memory import memory as _mem
                self._memory_ref = _mem
            except Exception:
                pass
        return True

    # ── Check ─────────────────────────────────────────────────────────────────

    async def check(self) -> WatchdogEvent:
        if not self._resolve():
            return self._make_event(
                EventType.TRADE_LOOP_OK,
                EventSeverity.INFO,
                "intelligence.autonomy not importable — trading engine not started yet",
            )

        loop = self._loop_ref
        is_running = loop.running
        cycle_count = loop.cycle_count if hasattr(loop, "cycle_count") else 0

        # ── Detect unexpected stop ────────────────────────────────────────────
        if self._expected_running and not is_running:
            return self._make_event(
                EventType.TRADE_LOOP_DEAD,
                EventSeverity.CRITICAL,
                "Trading loop stopped unexpectedly (loop.running is False)",
                details={"last_cycle": self._last_cycle, "cycle_count": cycle_count},
            )

        # ── Detect stale cycle ────────────────────────────────────────────────
        if is_running and cycle_count == self._last_cycle and self._last_cycle is not None:
            stale_for = time.monotonic() - self._last_cycle_time
            if stale_for > STALE_THRESHOLD:
                return self._make_event(
                    EventType.TRADE_LOOP_STALE,
                    EventSeverity.CRITICAL,
                    f"No cycle update in {stale_for:.0f}s (threshold {STALE_THRESHOLD}s)",
                    details={"last_cycle": self._last_cycle, "stale_s": round(stale_for)},
                )

        # ── Update cycle tracking ─────────────────────────────────────────────
        if cycle_count != self._last_cycle:
            self._last_cycle      = cycle_count
            self._last_cycle_time = time.monotonic()

        # ── Check last_result from memory for error patterns ──────────────────
        error_detail = ""
        if self._memory_ref:
            try:
                last_result = await self._memory_ref.retrieve(LOOP_AGENT_ID, "last_result")
                if last_result and "'status': 'error'" in last_result:
                    error_detail = f" (last_result contains error: {last_result[:80]})"
            except Exception:
                pass

        if error_detail:
            return self._make_event(
                EventType.TRADE_LOOP_OK,
                EventSeverity.WARNING,
                f"Loop running but last cycle reported an error{error_detail}",
                details={"cycle_count": cycle_count},
            )

        state = "RUNNING" if is_running else "IDLE"
        return self._make_event(
            EventType.TRADE_LOOP_OK,
            EventSeverity.INFO,
            f"Loop {state} (cycle #{cycle_count})",
            details={"running": is_running, "cycle_count": cycle_count},
        )

    # ── Heal ──────────────────────────────────────────────────────────────────

    async def heal(self, event: WatchdogEvent) -> bool:
        if not self.auto_heal:
            return False
        if not self._resolve():
            return False

        if event.event_type in (EventType.TRADE_LOOP_DEAD, EventType.TRADE_LOOP_STALE):
            loop = self._loop_ref
            if loop.running:
                # Force stop first
                loop.running = False
                await asyncio.sleep(0.5)

            # Determine agent_id from memory
            agent_id = LOOP_AGENT_ID
            if self._memory_ref:
                try:
                    stored_id = await self._memory_ref.retrieve(LOOP_AGENT_ID, "status")
                    if stored_id:
                        pass   # we know LOOP_AGENT_ID is correct
                except Exception:
                    pass

            loop.running = True
            asyncio.create_task(loop.run(agent_id), name="trade-loop-healed")
            self._expected_running = True
            self.log.warning("Trading loop restarted by watchdog (agent_id=%s)", agent_id)
            await asyncio.sleep(2.0)
            return loop.running

        return False

    def set_expected_running(self, expected: bool) -> None:
        """Tell the watchdog whether the loop is supposed to be running."""
        self._expected_running = expected
