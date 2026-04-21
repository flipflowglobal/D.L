"""
watchdog/agents/process_agent.py — Rust sidecar process watchdog.

Monitors dex-oracle (:9001) and tx-engine (:9002).

Health checks:
  1. Process alive    — subprocess.Popen.poll() is None
  2. HTTP /health     — GET returns 200 within 500 ms
  3. Response latency — warns if latency > threshold

Self-healing:
  - Dead process      → restart with exponential back-off
  - Unhealthy process → SIGTERM + restart
  - Max restarts      → give up, emit CRITICAL indefinitely
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

from watchdog.agents.base import WatchdogAgent
from watchdog.event_bus import EventBus, EventSeverity, EventType, WatchdogEvent

logger = logging.getLogger("watchdog.agent.process")

_REPO_ROOT = Path(__file__).parent.parent.parent

BACKOFF_BASE = 2.0     # seconds — doubles each restart
MAX_RESTARTS = 5
HEALTH_TIMEOUT = 0.5   # HTTP timeout for /health ping
LATENCY_WARN_MS = 200  # warn if health check takes longer


class ProcessAgent(WatchdogAgent):
    """
    Watchdog agent for a single Rust sidecar subprocess.

    Monitors process liveness and HTTP health endpoint.
    Restarts automatically with exponential back-off on failure.
    """

    def __init__(
        self,
        name:        str,             # e.g. "dex-oracle"
        binary:      Path,            # path to release binary
        health_url:  str,             # e.g. "http://127.0.0.1:9001/health"
        env:         dict,            # extra env vars for the process
        bus:         EventBus,
        interval:    float = 5.0,
    ) -> None:
        super().__init__(
            agent_id = f"process:{name}",
            source   = name,
            bus      = bus,
            interval = interval,
        )
        self.name        = name
        self.binary      = binary
        self.health_url  = health_url
        self.proc_env    = {**os.environ, **env}
        self._proc:      Optional[subprocess.Popen] = None
        self._restarts   = 0
        self._last_start = 0.0
        self._available  = binary.exists()
        self._http       = httpx.AsyncClient(timeout=HEALTH_TIMEOUT)

        if not self._available:
            self.log.warning("Binary not found: %s — process agent in monitor-only mode", binary)

    # ── Check ─────────────────────────────────────────────────────────────────

    async def check(self) -> WatchdogEvent:
        if not self._available:
            return self._make_event(
                EventType.PROCESS_OK,
                EventSeverity.INFO,
                f"Binary unavailable (not compiled) — skipping checks for {self.name}",
            )

        alive = self._is_alive()

        if not alive:
            return self._make_event(
                EventType.PROCESS_DEAD,
                EventSeverity.CRITICAL,
                f"{self.name} process is not running",
                details={"restarts": self._restarts},
            )

        # ── HTTP health check ─────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            resp = await self._http.get(self.health_url)
            latency_ms = (time.monotonic() - t0) * 1000

            if resp.status_code != 200:
                return self._make_event(
                    EventType.PROCESS_UNHEALTHY,
                    EventSeverity.WARNING,
                    f"{self.name} /health returned {resp.status_code} ({latency_ms:.0f}ms)",
                    details={"status_code": resp.status_code, "latency_ms": round(latency_ms, 1)},
                )

            if latency_ms > LATENCY_WARN_MS:
                return self._make_event(
                    EventType.PROCESS_UNHEALTHY,
                    EventSeverity.WARNING,
                    f"{self.name} health check slow: {latency_ms:.0f}ms (threshold {LATENCY_WARN_MS}ms)",
                    details={"latency_ms": round(latency_ms, 1)},
                )

            return self._make_event(
                EventType.PROCESS_OK,
                EventSeverity.INFO,
                f"{self.name} healthy ({latency_ms:.0f}ms)",
                details={"latency_ms": round(latency_ms, 1), "restarts": self._restarts},
            )

        except httpx.ConnectError:
            return self._make_event(
                EventType.PROCESS_UNHEALTHY,
                EventSeverity.WARNING,
                f"{self.name} process running but /health unreachable (port not bound yet?)",
            )
        except Exception as exc:
            return self._make_event(
                EventType.PROCESS_DEAD,
                EventSeverity.CRITICAL,
                f"{self.name} health check raised: {exc}",
            )

    # ── Heal ──────────────────────────────────────────────────────────────────

    async def heal(self, event: WatchdogEvent) -> bool:
        if self._restarts >= MAX_RESTARTS:
            self.log.error("%s exceeded max restarts (%d) — giving up", self.name, MAX_RESTARTS)
            return False

        backoff = BACKOFF_BASE ** self._restarts
        elapsed = time.monotonic() - self._last_start
        if elapsed < backoff:
            wait = backoff - elapsed
            self.log.info("%s back-off: waiting %.1fs before restart", self.name, wait)
            await asyncio.sleep(wait)

        # Terminate existing process if alive
        if self._is_alive() and self._proc:
            try:
                self._proc.send_signal(signal.SIGTERM)
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass

        # Launch fresh process
        try:
            self._proc = subprocess.Popen(
                [str(self.binary)],
                env=self.proc_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._last_start = time.monotonic()
            self._restarts  += 1
            self.log.warning(
                "Restarted %s (pid=%d, attempt #%d)",
                self.name, self._proc.pid, self._restarts,
            )
            # Allow process to bind port
            await asyncio.sleep(1.5)
            return self._is_alive()
        except Exception as exc:
            self.log.error("Restart of %s failed: %s", self.name, exc)
            return False

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start_process(self) -> bool:
        """Launch the sidecar (called once at startup by the kernel)."""
        if not self._available or self._is_alive():
            return self._available
        try:
            self._proc = subprocess.Popen(
                [str(self.binary)],
                env=self.proc_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._last_start = time.monotonic()
            self.log.info("%s launched (pid=%d)", self.name, self._proc.pid)
            return True
        except Exception as exc:
            self.log.error("Failed to launch %s: %s", self.name, exc)
            return False

    def stop_process(self) -> None:
        """Gracefully terminate the sidecar."""
        if self._proc and self._is_alive():
            try:
                self._proc.send_signal(signal.SIGTERM)
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                self.log.info("%s terminated", self.name)
            except Exception as exc:
                self.log.warning("Stop error for %s: %s", self.name, exc)
        self._proc = None

    async def close(self) -> None:
        """Stop the poll loop and terminate the managed process."""
        await self.stop()
        self.stop_process()
        await self._http.aclose()
