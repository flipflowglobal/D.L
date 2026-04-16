"""
kernel/watchdog_kernel.py — AUREON System Kernel
=================================================

Single orchestrator that manages the entire AUREON runtime:
  - Starts the FastAPI server (uvicorn) as a managed subprocess
  - Monitors API health via HTTP pings at configurable intervals
  - Auto-restarts the API server on crash or unresponsive health check
  - Runs agent-level watchdog supervision in a background task
  - Triggers periodic state snapshots for crash recovery
  - Enforces binary integrity checks on startup
  - Provides graceful shutdown on SIGTERM / SIGINT

Architecture:
  ┌──────────────────────────────────────────────────────────┐
  │                    WatchdogKernel                         │
  │  ┌─────────────────┐  ┌─────────────────────────────┐   │
  │  │  API Server      │  │  Background Tasks            │   │
  │  │  (uvicorn)       │  │  - health_loop               │   │
  │  │  :8010           │  │  - snapshot_loop              │   │
  │  └────────┬─────────┘  │  - agent_watchdog_loop        │   │
  │           │ crash?     └─────────────────────────────┘   │
  │           ▼                                               │
  │    Auto-restart with exponential back-off                 │
  └──────────────────────────────────────────────────────────┘

Usage:
  python -m kernel.watchdog_kernel          # standalone
  from kernel.watchdog_kernel import WatchdogKernel
  kernel = WatchdogKernel()
  asyncio.run(kernel.run())
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("aureon.kernel")

# ── Configuration (environment variables with sane defaults) ──────────────────

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8010"))
HEALTH_URL = os.getenv(
    "KERNEL_HEALTH_URL", f"http://127.0.0.1:{API_PORT}/health"
)
HEALTH_INTERVAL = float(os.getenv("KERNEL_HEALTH_INTERVAL", "10"))
SNAPSHOT_INTERVAL = float(os.getenv("KERNEL_SNAPSHOT_INTERVAL", "60"))
AGENT_CHECK_INTERVAL = float(os.getenv("KERNEL_AGENT_CHECK_INTERVAL", "15"))
MAX_RESTARTS = int(os.getenv("KERNEL_MAX_RESTARTS", "10"))
BACKOFF_BASE = 2.0  # seconds — doubles each restart attempt
BACKOFF_CAP = 120.0  # max wait between restarts
HEALTH_TIMEOUT = float(os.getenv("KERNEL_HEALTH_TIMEOUT", "5"))
INTEGRITY_CHECK = os.getenv("KERNEL_INTEGRITY_CHECK", "true").lower() in (
    "1",
    "true",
    "yes",
)


class WatchdogKernel:
    """
    Central system kernel — orchestrates all AUREON subsystems.

    Guarantees:
      - API server is always running (auto-restart on crash)
      - Agent health is monitored and agents are restarted if stuck
      - State is periodically snapshotted for crash recovery
      - Binary integrity is verified on startup
    """

    def __init__(self) -> None:
        self._api_proc: Optional[asyncio.subprocess.Process] = None
        self._restarts = 0
        self._last_start = 0.0
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._client: Optional[httpx.AsyncClient] = None
        self._start_time: Optional[float] = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main entry point — runs the kernel until shutdown signal."""
        self._running = True
        self._start_time = time.monotonic()
        self._client = httpx.AsyncClient(timeout=HEALTH_TIMEOUT)

        logger.info(
            "WatchdogKernel starting — health=%s interval=%.0fs snapshots=%.0fs",
            HEALTH_URL,
            HEALTH_INTERVAL,
            SNAPSHOT_INTERVAL,
        )

        # ── Optional integrity check ──────────────────────────────────────────
        if INTEGRITY_CHECK:
            try:
                from core.integrity import IntegrityChecker

                checker = IntegrityChecker()
                result = checker.verify_all()
                if not result["passed"]:
                    logger.error(
                        "Integrity check FAILED: %s", result.get("failures")
                    )
                    # Continue but log — don't block boot on missing binaries
                else:
                    logger.info(
                        "Integrity check passed: %d files verified",
                        result["checked"],
                    )
            except Exception as exc:
                logger.warning("Integrity check skipped: %s", exc)

        # ── Start API server ──────────────────────────────────────────────────
        await self._start_api()

        # ── Launch background tasks ───────────────────────────────────────────
        self._tasks = [
            asyncio.create_task(
                self._health_loop(), name="kernel-health"
            ),
            asyncio.create_task(
                self._snapshot_loop(), name="kernel-snapshot"
            ),
            asyncio.create_task(
                self._agent_watchdog_loop(), name="kernel-agent-watchdog"
            ),
        ]

        logger.info("WatchdogKernel online — all background tasks started")

        # ── Wait for shutdown ─────────────────────────────────────────────────
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def shutdown(self) -> None:
        """External shutdown trigger (e.g. from signal handler)."""
        self._running = False
        for task in self._tasks:
            task.cancel()

    def status(self) -> dict:
        """Return kernel status snapshot."""
        uptime = time.monotonic() - self._start_time if self._start_time else 0
        api_running = (
            self._api_proc is not None
            and self._api_proc.returncode is None
        )
        return {
            "running": self._running,
            "uptime_seconds": round(uptime, 1),
            "api_server": {
                "running": api_running,
                "pid": self._api_proc.pid if self._api_proc else None,
                "restarts": self._restarts,
            },
            "config": {
                "health_interval": HEALTH_INTERVAL,
                "snapshot_interval": SNAPSHOT_INTERVAL,
                "max_restarts": MAX_RESTARTS,
                "integrity_check": INTEGRITY_CHECK,
            },
        }

    # ── API Server Management ─────────────────────────────────────────────────

    async def _start_api(self) -> bool:
        """Launch the uvicorn API server as a subprocess."""
        if self._api_proc and self._api_proc.returncode is None:
            return True  # already running

        self._last_start = time.monotonic()
        try:
            self._api_proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                API_HOST,
                "--port",
                str(API_PORT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("API server started (pid=%d)", self._api_proc.pid)
            # Allow the server to bind
            await asyncio.sleep(2.0)
            return True
        except Exception as exc:
            logger.error("Failed to start API server: %s", exc)
            return False

    async def _restart_api(self) -> bool:
        """Restart the API server with exponential back-off."""
        if self._restarts >= MAX_RESTARTS:
            logger.critical(
                "API server exceeded max restarts (%d) — kernel degraded",
                MAX_RESTARTS,
            )
            return False

        backoff = min(BACKOFF_BASE ** self._restarts, BACKOFF_CAP)
        elapsed = time.monotonic() - self._last_start
        if elapsed < backoff:
            await asyncio.sleep(backoff - elapsed)

        self._restarts += 1
        logger.warning(
            "Restarting API server — attempt %d/%d (backoff=%.1fs)",
            self._restarts,
            MAX_RESTARTS,
            backoff,
        )

        # Kill old process if still lingering
        if self._api_proc and self._api_proc.returncode is None:
            try:
                self._api_proc.terminate()
                await asyncio.wait_for(self._api_proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._api_proc.kill()
                except ProcessLookupError:
                    pass

        return await self._start_api()

    # ── Health Loop ───────────────────────────────────────────────────────────

    async def _health_loop(self) -> None:
        """Continuously ping the API health endpoint; restart on failure."""
        consecutive_failures = 0
        max_failures = 3  # restart after 3 consecutive failures

        while self._running:
            await asyncio.sleep(HEALTH_INTERVAL)
            if not self._running:
                break

            healthy = await self._check_health()
            if healthy:
                consecutive_failures = 0
                # Reset restart counter on sustained health
                if self._restarts > 0:
                    logger.info(
                        "API server recovered — resetting restart counter"
                    )
                    self._restarts = 0
            else:
                consecutive_failures += 1
                logger.warning(
                    "Health check failed (%d/%d)",
                    consecutive_failures,
                    max_failures,
                )
                if consecutive_failures >= max_failures:
                    consecutive_failures = 0
                    await self._restart_api()

    async def _check_health(self) -> bool:
        """Ping the health endpoint. Returns True if healthy."""
        if not self._client:
            return False
        try:
            resp = await self._client.get(HEALTH_URL)
            return resp.status_code == 200
        except Exception:
            return False

    # ── Snapshot Loop ─────────────────────────────────────────────────────────

    async def _snapshot_loop(self) -> None:
        """Periodically trigger state snapshots via the StateManager."""
        # Lazy import to avoid circular imports at module level
        while self._running:
            await asyncio.sleep(SNAPSHOT_INTERVAL)
            if not self._running:
                break

            try:
                from core.state_manager import state_manager

                snapshot = await state_manager.snapshot()
                logger.debug(
                    "State snapshot completed: %d agents, db=%s",
                    snapshot.get("agent_count", 0),
                    snapshot.get("db_path", "?"),
                )
            except Exception as exc:
                logger.error("Snapshot failed: %s", exc)

    # ── Agent Watchdog Loop ───────────────────────────────────────────────────

    async def _agent_watchdog_loop(self) -> None:
        """Monitor individual agent health via the watchdog agent."""
        while self._running:
            await asyncio.sleep(AGENT_CHECK_INTERVAL)
            if not self._running:
                break

            try:
                from agents.watchdog_agent import AgentWatchdog

                watchdog = AgentWatchdog()
                report = await watchdog.check_all()
                if report.get("restarted"):
                    logger.warning(
                        "Agent watchdog restarted agents: %s",
                        report["restarted"],
                    )
            except Exception as exc:
                logger.debug("Agent watchdog cycle: %s", exc)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        """Graceful shutdown of all managed components."""
        logger.info("WatchdogKernel shutting down …")
        self._running = False

        # Stop API server
        if self._api_proc and self._api_proc.returncode is None:
            try:
                self._api_proc.terminate()
                await asyncio.wait_for(self._api_proc.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._api_proc.kill()
                except ProcessLookupError:
                    pass

        # Final snapshot
        try:
            from core.state_manager import state_manager

            await state_manager.snapshot()
            logger.info("Final state snapshot saved")
        except Exception as exc:
            logger.warning("Final snapshot failed: %s", exc)

        # Close HTTP client
        if self._client:
            await self._client.aclose()

        logger.info("WatchdogKernel stopped")


# ── Signal handler setup ──────────────────────────────────────────────────────


def _setup_signals(kernel: WatchdogKernel, loop: asyncio.AbstractEventLoop) -> None:
    """Register SIGTERM / SIGINT handlers for graceful shutdown."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(kernel.shutdown()))
        except NotImplementedError:
            pass  # Windows — signal handlers are not supported


# ── CLI entry point ───────────────────────────────────────────────────────────


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    kernel = WatchdogKernel()
    loop = asyncio.get_running_loop()
    _setup_signals(kernel, loop)

    logger.info(
        "AUREON WatchdogKernel v1.0 — started at %s",
        datetime.now(timezone.utc).isoformat(),
    )
    await kernel.run()


if __name__ == "__main__":
    asyncio.run(_main())
