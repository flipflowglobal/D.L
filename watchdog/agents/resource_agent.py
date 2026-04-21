"""
watchdog/agents/resource_agent.py — Host resource watchdog.

Monitors system-level resources on the host machine:
  - CPU usage   (per-core + aggregate, 1-minute average)
  - Memory RSS  (process + system-wide)
  - Disk usage  (repo partition)
  - Open file descriptors (this process)

Self-healing:
  - High CPU     → trigger gc.collect() + log (no process kill)
  - Low disk     → log CRITICAL + attempt deletion of .bak / .log > 30 days
  - Memory leak  → log CRITICAL (operator must intervene)
"""

from __future__ import annotations

import gc
import logging
import os
import time
from pathlib import Path
from typing import Optional

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

from watchdog.agents.base import WatchdogAgent
from watchdog.event_bus import EventBus, EventSeverity, EventType, WatchdogEvent

logger = logging.getLogger("watchdog.agent.resource")

_REPO_ROOT = Path(__file__).parent.parent.parent

# Thresholds
CPU_WARN_PCT    = 85.0   # % aggregate CPU — warn
CPU_CRIT_PCT    = 95.0   # % aggregate CPU — critical
MEM_WARN_PCT    = 80.0   # % system memory used — warn
MEM_CRIT_PCT    = 92.0   # % system memory used — critical
DISK_WARN_PCT   = 80.0   # % disk used — warn
DISK_CRIT_PCT   = 90.0   # % disk used — critical
FD_WARN         = 800    # open file descriptors — warn
FD_CRIT         = 1_000  # open file descriptors — critical


class ResourceAgent(WatchdogAgent):
    """
    System resource watchdog.

    Emits RESOURCE_* events when CPU, memory, or disk thresholds are breached.
    Performs light self-healing (GC, stale file cleanup) on CRITICAL events.
    """

    def __init__(
        self,
        bus:      EventBus,
        interval: float = 30.0,
        repo_root: Path = _REPO_ROOT,
    ) -> None:
        super().__init__(
            agent_id = "resource:host",
            source   = "psutil",
            bus      = bus,
            interval = interval,
        )
        self.repo_root = repo_root
        self._proc: Optional["psutil.Process"] = None
        if _PSUTIL:
            try:
                self._proc = psutil.Process(os.getpid())
                # Warm up — first cpu_percent() call always returns 0
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

    # ── Check ─────────────────────────────────────────────────────────────────

    async def check(self) -> WatchdogEvent:
        if not _PSUTIL:
            return self._make_event(
                EventType.RESOURCE_OK,
                EventSeverity.INFO,
                "psutil not installed — resource monitoring disabled",
            )

        details: dict = {}

        # ── CPU ───────────────────────────────────────────────────────────────
        try:
            cpu_pct = psutil.cpu_percent(interval=None)
            details["cpu_pct"] = round(cpu_pct, 1)
        except Exception:
            cpu_pct = 0.0

        # ── Memory ────────────────────────────────────────────────────────────
        try:
            vm = psutil.virtual_memory()
            mem_pct = vm.percent
            mem_used_mb = vm.used / (1024 * 1024)
            details["mem_pct"]     = round(mem_pct, 1)
            details["mem_used_mb"] = round(mem_used_mb)
        except Exception:
            mem_pct = 0.0

        # ── Process RSS ───────────────────────────────────────────────────────
        try:
            if self._proc:
                rss_mb = self._proc.memory_info().rss / (1024 * 1024)
                details["proc_rss_mb"] = round(rss_mb, 1)
        except Exception:
            pass

        # ── Disk ──────────────────────────────────────────────────────────────
        try:
            disk = psutil.disk_usage(str(self.repo_root))
            disk_pct = disk.percent
            disk_free_gb = disk.free / (1024 ** 3)
            details["disk_pct"]     = round(disk_pct, 1)
            details["disk_free_gb"] = round(disk_free_gb, 2)
        except Exception:
            disk_pct = 0.0

        # ── File descriptors ──────────────────────────────────────────────────
        try:
            if self._proc:
                fds = self._proc.num_fds()
                details["open_fds"] = fds
            else:
                fds = 0
        except Exception:
            fds = 0

        # ── Classify ──────────────────────────────────────────────────────────
        if disk_pct >= DISK_CRIT_PCT:
            return self._make_event(
                EventType.RESOURCE_DISK_CRITICAL,
                EventSeverity.CRITICAL,
                f"Disk critically full: {disk_pct:.1f}% used, {disk_free_gb:.2f} GB free",
                details=details,
            )
        if cpu_pct >= CPU_CRIT_PCT:
            return self._make_event(
                EventType.RESOURCE_CPU_HIGH,
                EventSeverity.CRITICAL,
                f"CPU critically high: {cpu_pct:.1f}%",
                details=details,
            )
        if mem_pct >= MEM_CRIT_PCT:
            return self._make_event(
                EventType.RESOURCE_MEM_HIGH,
                EventSeverity.CRITICAL,
                f"Memory critically high: {mem_pct:.1f}%",
                details=details,
            )
        if fds >= FD_CRIT:
            return self._make_event(
                EventType.RESOURCE_FD_HIGH,
                EventSeverity.CRITICAL,
                f"File descriptor count critical: {fds}",
                details=details,
            )
        if disk_pct >= DISK_WARN_PCT or cpu_pct >= CPU_WARN_PCT or mem_pct >= MEM_WARN_PCT or fds >= FD_WARN:
            return self._make_event(
                EventType.RESOURCE_OK,
                EventSeverity.WARNING,
                f"Resources elevated — CPU {cpu_pct:.1f}% MEM {mem_pct:.1f}% DISK {disk_pct:.1f}% FDs {fds}",
                details=details,
            )

        return self._make_event(
            EventType.RESOURCE_OK,
            EventSeverity.INFO,
            f"CPU {cpu_pct:.1f}% | MEM {mem_pct:.1f}% | DISK {disk_pct:.1f}% | FDs {fds}",
            details=details,
        )

    # ── Heal ──────────────────────────────────────────────────────────────────

    async def heal(self, event: WatchdogEvent) -> bool:
        if event.event_type == EventType.RESOURCE_CPU_HIGH:
            collected = gc.collect(generation=2)
            self.log.warning("GC collect triggered — freed %d objects", collected)
            return True   # best-effort

        if event.event_type == EventType.RESOURCE_DISK_CRITICAL:
            return self._cleanup_stale_files()

        if event.event_type == EventType.RESOURCE_MEM_HIGH:
            collected = gc.collect(generation=2)
            self.log.warning(
                "Memory critical — GC freed %d objects. Manual investigation required.", collected
            )
            return False   # operator must intervene

        return False

    def _cleanup_stale_files(self) -> bool:
        """Delete .bak and .log files older than 30 days from the repo root."""
        cutoff = time.time() - 30 * 86_400
        removed = 0
        errors  = 0
        for pattern in ("**/*.bak", "**/*.log"):
            for f in self.repo_root.rglob(pattern.lstrip("**/")):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                        self.log.info("Deleted stale file: %s", f)
                except Exception as exc:
                    errors += 1
                    self.log.warning("Could not delete %s: %s", f, exc)
        self.log.warning(
            "Stale file cleanup: removed %d files, %d errors", removed, errors
        )
        return removed > 0 or errors == 0
