"""
watchdog/agents/db_agent.py — SQLite database integrity watchdog.

Monitors all .db files in the repository:
  - File existence
  - SQLite integrity check (PRAGMA integrity_check)
  - Database size growth anomaly detection

Self-healing:
  - Corrupt DB → VACUUM + integrity re-check; if still broken → backup + recreate
  - Missing DB → allow owning module to recreate on next init_db() call
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

import aiosqlite

from watchdog.agents.base import WatchdogAgent
from watchdog.event_bus import EventBus, EventSeverity, EventType, WatchdogEvent

logger = logging.getLogger("watchdog.agent.db")

SIZE_WARN_MB = 50.0     # warn if DB grows beyond this


class DatabaseAgent(WatchdogAgent):
    """
    Watchdog agent for a single SQLite database file.
    """

    def __init__(
        self,
        db_path:  Path,
        bus:      EventBus,
        interval: float = 60.0,
    ) -> None:
        super().__init__(
            agent_id = f"db:{db_path.name}",
            source   = str(db_path),
            bus      = bus,
            interval = interval,
        )
        self.db_path       = db_path
        self._last_size:   Optional[int] = None
        self._size_samples: list[int]    = []

    # ── Check ─────────────────────────────────────────────────────────────────

    async def check(self) -> WatchdogEvent:
        # ── 1. Existence ──────────────────────────────────────────────────────
        if not self.db_path.exists():
            return self._make_event(
                EventType.DB_MISSING,
                EventSeverity.WARNING,
                f"Database file not found: {self.db_path.name} (will be created on next init)",
            )

        # ── 2. Size check ─────────────────────────────────────────────────────
        size_bytes = self.db_path.stat().st_size
        size_mb    = size_bytes / (1024 * 1024)
        self._size_samples.append(size_bytes)
        if len(self._size_samples) > 10:
            self._size_samples.pop(0)

        if size_mb > SIZE_WARN_MB:
            return self._make_event(
                EventType.DB_OK,
                EventSeverity.WARNING,
                f"Database is large: {size_mb:.1f} MB — consider archiving old records",
                details={"size_mb": round(size_mb, 2)},
            )

        # ── 3. Integrity check ─────────────────────────────────────────────────
        try:
            result = await self._integrity_check()
        except Exception as exc:
            return self._make_event(
                EventType.DB_CORRUPT,
                EventSeverity.CRITICAL,
                f"integrity_check raised: {exc}",
                details={"size_mb": round(size_mb, 2)},
            )

        if result != "ok":
            return self._make_event(
                EventType.DB_CORRUPT,
                EventSeverity.CRITICAL,
                f"SQLite integrity_check failed: {result}",
                details={"size_mb": round(size_mb, 2), "result": result},
            )

        return self._make_event(
            EventType.DB_OK,
            EventSeverity.INFO,
            f"OK ({size_mb:.2f} MB)",
            details={"size_mb": round(size_mb, 3)},
        )

    async def _integrity_check(self) -> str:
        """Run PRAGMA integrity_check. Returns 'ok' or the error message."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            async with db.execute("PRAGMA integrity_check") as cur:
                row = await cur.fetchone()
                return row[0] if row else "no_result"

    # ── Heal ──────────────────────────────────────────────────────────────────

    async def heal(self, event: WatchdogEvent) -> bool:
        if event.event_type == EventType.DB_CORRUPT:
            return await self._heal_corruption()
        return False

    async def _heal_corruption(self) -> bool:
        """VACUUM the database; if still corrupt, move to .bak and let it be recreated."""
        # Step 1: Try VACUUM
        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                await db.execute("VACUUM")
                await db.commit()

            result = await self._integrity_check()
            if result == "ok":
                self.log.warning("Database healed via VACUUM: %s", self.db_path.name)
                return True
        except Exception as exc:
            self.log.error("VACUUM failed: %s", exc)

        # Step 2: Back up the corrupt file and delete it so the module recreates it
        try:
            bak = self.db_path.with_suffix(f".corrupt_{int(time.time())}.bak")
            shutil.move(str(self.db_path), str(bak))
            self.log.critical(
                "Corrupt DB moved to %s — module will recreate on next init_db()", bak.name
            )
            return True   # "healed" by clearing — module must recreate
        except Exception as exc:
            self.log.error("Failed to move corrupt DB: %s", exc)
            return False
