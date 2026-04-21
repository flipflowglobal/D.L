"""
watchdog/agents/service_agent.py — HTTP service health watchdog.

Monitors FastAPI services (main.py on :8000, aureon_server.py on :8010,
aureon_onthedl.py on :8010) via their /health or / endpoints.

Self-healing:
  - Service down → restart via subprocess (uvicorn)
  - Service degraded (slow responses) → WARNING only (no restart)
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from watchdog.agents.base import WatchdogAgent
from watchdog.event_bus import EventBus, EventSeverity, EventType, WatchdogEvent

logger = logging.getLogger("watchdog.agent.service")

_REPO_ROOT    = Path(__file__).parent.parent.parent
LATENCY_WARN  = 500    # ms — warn if response takes longer
HTTP_TIMEOUT  = 2.0    # seconds for health check


class ServiceAgent(WatchdogAgent):
    """
    Watchdog agent for a uvicorn/FastAPI HTTP service.

    Polls the service health endpoint and optionally restarts
    the service process on failure.
    """

    def __init__(
        self,
        name:         str,             # human label, e.g. "aureon-api"
        host:         str,
        port:         int,
        entry_module: str,             # e.g. "main:app"
        health_path:  str = "/health",
        bus:          EventBus  = None,
        interval:     float     = 10.0,
        auto_restart: bool      = True,
    ) -> None:
        super().__init__(
            agent_id = f"service:{name}",
            source   = f"http://{host}:{port}",
            bus      = bus,
            interval = interval,
        )
        self.name         = name
        self.host         = host
        self.port         = port
        self.entry_module = entry_module
        self.health_url   = f"http://{host}:{port}{health_path}"
        self.auto_restart = auto_restart
        self._http        = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        self._proc:       Optional[subprocess.Popen] = None
        self._restarts    = 0

    # ── Check ─────────────────────────────────────────────────────────────────

    async def check(self) -> WatchdogEvent:
        t0 = time.monotonic()
        try:
            resp = await self._http.get(self.health_url)
            latency_ms = (time.monotonic() - t0) * 1000

            if resp.status_code not in (200, 204):
                return self._make_event(
                    EventType.SERVICE_DEGRADED,
                    EventSeverity.WARNING,
                    f"{self.name} returned HTTP {resp.status_code} ({latency_ms:.0f}ms)",
                    details={"status_code": resp.status_code, "latency_ms": round(latency_ms)},
                )

            if latency_ms > LATENCY_WARN:
                return self._make_event(
                    EventType.SERVICE_DEGRADED,
                    EventSeverity.WARNING,
                    f"{self.name} slow response: {latency_ms:.0f}ms",
                    details={"latency_ms": round(latency_ms)},
                )

            return self._make_event(
                EventType.SERVICE_OK,
                EventSeverity.INFO,
                f"{self.name} healthy ({latency_ms:.0f}ms)",
                details={"latency_ms": round(latency_ms), "restarts": self._restarts},
            )

        except (httpx.ConnectError, httpx.ConnectTimeout):
            return self._make_event(
                EventType.SERVICE_DOWN,
                EventSeverity.CRITICAL,
                f"{self.name} unreachable on port {self.port}",
                details={"restarts": self._restarts},
            )
        except Exception as exc:
            return self._make_event(
                EventType.SERVICE_DOWN,
                EventSeverity.CRITICAL,
                f"{self.name} health check raised: {exc}",
            )

    # ── Heal ──────────────────────────────────────────────────────────────────

    async def heal(self, event: WatchdogEvent) -> bool:
        if not self.auto_restart:
            self.log.warning("Auto-restart disabled for %s — manual intervention required", self.name)
            return False

        self._restarts += 1
        self.log.warning("Restarting %s (attempt #%d)", self.name, self._restarts)

        # Kill existing process if still running
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

        # Re-launch with uvicorn
        try:
            cmd = [
                sys.executable, "-m", "uvicorn",
                self.entry_module,
                "--host", self.host,
                "--port", str(self.port),
                "--log-level", "warning",
            ]
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(_REPO_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
            )
            self.log.warning("%s relaunched (pid=%d)", self.name, self._proc.pid)
            # Wait for service to bind
            await asyncio.sleep(2.0)
            # Verify it's actually up now
            try:
                resp = await self._http.get(self.health_url)
                return resp.status_code in (200, 204)
            except Exception:
                return False
        except Exception as exc:
            self.log.error("Restart of %s failed: %s", self.name, exc)
            return False

    async def close(self) -> None:
        await self.stop()
        await self._http.aclose()
