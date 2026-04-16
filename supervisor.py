"""
supervisor.py — Cross-language process supervisor for AUREON.

Manages the Rust sidecar processes (dex-oracle, tx-engine) with:
  - Health monitoring every HEALTH_INTERVAL seconds
  - Automatic restart on crash (exponential back-off, max 5 retries)
  - Cross-language fallback: if Rust sidecar is down, Python path activates
  - Fault isolation: one sidecar crash never takes down the others

Architecture:
  ┌──────────────────────────────────────────────────────┐
  │                   Supervisor                         │
  │  ┌─────────────┐   ┌─────────────┐                  │
  │  │ dex-oracle  │   │  tx-engine  │  ← Rust sidecars │
  │  │  :9001      │   │   :9002     │                   │
  │  └──────┬──────┘   └──────┬──────┘                  │
  │         │ crash?           │ crash?                  │
  │         ▼                  ▼                         │
  │  Python async DEX    Python web3.py signing          │
  │  fallback path       fallback path                   │
  └──────────────────────────────────────────────────────┘

Usage:
  from supervisor import Supervisor
  sup = Supervisor()
  await sup.start()          # launch both sidecars
  await sup.stop()           # graceful shutdown
  healthy = sup.dex_ok()     # True if dex-oracle is alive
  healthy = sup.tx_ok()      # True if tx-engine is alive
"""

import asyncio
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("aureon.supervisor")

# ── Sidecar binary paths ──────────────────────────────────────────────────────

_REPO_ROOT  = Path(__file__).parent
DEX_BINARY  = _REPO_ROOT / "dex-oracle" / "target" / "release" / "dex-oracle"
TX_BINARY   = _REPO_ROOT / "tx-engine"  / "target" / "release" / "tx-engine"

DEX_ADDR    = os.getenv("DEX_ORACLE_ADDR", "127.0.0.1:9001")
TX_ADDR     = os.getenv("TX_ENGINE_ADDR",  "127.0.0.1:9002")
DEX_URL     = f"http://{DEX_ADDR}"
TX_URL      = f"http://{TX_ADDR}"

HEALTH_INTERVAL = float(os.getenv("SUPERVISOR_HEALTH_INTERVAL", "5"))
MAX_RESTARTS    = int(os.getenv("SUPERVISOR_MAX_RESTARTS", "5"))
BACKOFF_BASE    = 2.0   # seconds — doubles each restart attempt


class SidecarProcess:
    """Manages a single Rust sidecar subprocess."""

    def __init__(self, name: str, binary: Path, health_url: str, env: dict):
        self.name        = name
        self.binary      = binary
        self.health_url  = health_url
        self.env         = {**os.environ, **env}
        self._proc: Optional[subprocess.Popen] = None
        self._restarts   = 0
        self._last_start = 0.0
        self._healthy    = False
        self._available  = binary.exists()

        if not self._available:
            logger.warning("%s binary not found at %s — fallback mode only", name, binary)

    # ── Process lifecycle ─────────────────────────────────────────────────────

    def start(self) -> bool:
        """Launch the sidecar process. Returns True if started."""
        if not self._available:
            return False
        if self._proc and self._proc.poll() is None:
            return True  # already running

        self._last_start = time.monotonic()
        try:
            self._proc = subprocess.Popen(
                [str(self.binary)],
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            logger.info("%s started (pid=%d)", self.name, self._proc.pid)
            return True
        except Exception as exc:
            logger.error("%s failed to start: %s", self.name, exc)
            return False

    def stop(self):
        """Gracefully terminate the sidecar."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGTERM)
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                logger.info("%s stopped", self.name)
            except Exception as exc:
                logger.warning("%s stop error: %s", self.name, exc)
        self._proc = None
        self._healthy = False

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def healthy(self) -> bool:
        return self._healthy

    @healthy.setter
    def healthy(self, value: bool):
        if value != self._healthy:
            logger.info("%s health changed: %s → %s", self.name,
                        "UP" if self._healthy else "DOWN",
                        "UP" if value else "DOWN")
        self._healthy = value

    # ── Restart logic (exponential back-off) ─────────────────────────────────

    async def maybe_restart(self) -> bool:
        """
        Restart if process has died and we haven't exceeded MAX_RESTARTS.
        Uses exponential back-off to avoid hammering a broken binary.
        Returns True if a restart was attempted.
        """
        if not self._available:
            return False
        if self.is_running():
            return False
        if self._restarts >= MAX_RESTARTS:
            if self._restarts == MAX_RESTARTS:
                logger.error("%s exceeded max restarts (%d) — giving up", self.name, MAX_RESTARTS)
                self._restarts += 1  # prevent repeated log
            return False

        backoff = BACKOFF_BASE ** self._restarts
        elapsed = time.monotonic() - self._last_start
        if elapsed < backoff:
            return False  # still in back-off window

        logger.warning("%s crashed — restart attempt %d (back-off=%.1fs)",
                       self.name, self._restarts + 1, backoff)
        self._restarts += 1
        self.start()
        return True

    def reset_restarts(self):
        """Called when sidecar comes back healthy — reset the counter."""
        if self._restarts > 0:
            logger.info("%s recovered — resetting restart counter", self.name)
        self._restarts = 0


# ── Supervisor ────────────────────────────────────────────────────────────────

class Supervisor:
    """
    Launches, monitors, and auto-restarts Rust sidecar processes.
    Exposes health status so the Python trading engine knows whether
    to use the Rust fast-path or the Python fallback path.
    """

    def __init__(self):
        rpc_url    = os.getenv("RPC_URL", os.getenv("ETH_RPC", ""))
        chain_id   = os.getenv("CHAIN_ID", "1")
        dry_run    = os.getenv("DRY_RUN", "true")
        priv_key   = os.getenv("PRIVATE_KEY", "")
        profit_w   = os.getenv("PROFIT_WALLET", os.getenv("WALLET_ADDRESS", ""))

        self._dex = SidecarProcess(
            name="dex-oracle",
            binary=DEX_BINARY,
            health_url=f"{DEX_URL}/health",
            env={
                "DEX_ORACLE_ADDR": DEX_ADDR,
                "RPC_URL":         rpc_url,
                "DRY_RUN":         dry_run,
                "RUST_LOG":        "warn",
            },
        )

        self._tx = SidecarProcess(
            name="tx-engine",
            binary=TX_BINARY,
            health_url=f"{TX_URL}/health",
            env={
                "TX_ENGINE_ADDR":  TX_ADDR,
                "RPC_URL":         rpc_url,
                "CHAIN_ID":        chain_id,
                "DRY_RUN":         dry_run,
                "PRIVATE_KEY":     priv_key,
                "PROFIT_WALLET":   profit_w,
                "RUST_LOG":        "warn",
            },
        )

        self._client:  Optional[httpx.AsyncClient] = None
        self._monitor: Optional[asyncio.Task] = None
        self._running  = False

    # ── Public API ────────────────────────────────────────────────────────────

    def dex_ok(self) -> bool:
        """True if dex-oracle is running and last health check passed."""
        return self._dex.healthy

    def tx_ok(self) -> bool:
        """True if tx-engine is running and last health check passed."""
        return self._tx.healthy

    async def start(self):
        """Start both sidecars and begin health monitoring."""
        self._client  = httpx.AsyncClient(timeout=2.0)
        self._running = True

        self._dex.start()
        self._tx.start()

        # Give processes 1.5s to bind their ports before first health check
        await asyncio.sleep(1.5)

        self._monitor = asyncio.create_task(self._monitor_loop(), name="supervisor-monitor")
        logger.info("Supervisor started — monitoring every %.1fs", HEALTH_INTERVAL)

    async def stop(self):
        """Graceful shutdown: stop monitor then terminate sidecars."""
        self._running = False
        if self._monitor:
            self._monitor.cancel()
            try:
                await self._monitor
            except asyncio.CancelledError:
                pass

        self._dex.stop()
        self._tx.stop()

        if self._client:
            await self._client.aclose()
        logger.info("Supervisor stopped")

    # ── Monitor loop ──────────────────────────────────────────────────────────

    async def _monitor_loop(self):
        """
        Runs forever: health-checks both sidecars, restarts on crash,
        resets restart counter on recovery.
        """
        while self._running:
            await asyncio.gather(
                self._check(self._dex),
                self._check(self._tx),
            )
            await asyncio.sleep(HEALTH_INTERVAL)

    async def _check(self, sidecar: SidecarProcess):
        """Ping health endpoint and handle crash/recovery."""
        # Attempt restart if process died
        await sidecar.maybe_restart()
        if not sidecar.is_running():
            sidecar.healthy = False
            return

        # Ping health endpoint
        try:
            resp = await self._client.get(sidecar.health_url)
            was_healthy = sidecar.healthy
            sidecar.healthy = (resp.status_code == 200)
            if sidecar.healthy and not was_healthy:
                sidecar.reset_restarts()
        except Exception as exc:
            logger.debug("Health check failed for %s: %s", sidecar.name, exc)
            sidecar.healthy = False

    # ── Convenience: proxy DEX price fetch ───────────────────────────────────

    async def fetch_prices(self) -> Optional[dict]:
        """
        Try dex-oracle first; return None if unavailable so caller can
        activate the Python fallback path.
        """
        if not self._client or not self._dex.healthy:
            return None
        try:
            resp = await self._client.get(f"{DEX_URL}/prices")
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug("dex-oracle price fetch failed: %s", exc)
            self._dex.healthy = False
        return None

    # ── Convenience: proxy TX send ────────────────────────────────────────

    async def send_eth(self, to: str, value_eth: float, data: str = "") -> Optional[dict]:
        """
        Try tx-engine first; return None if unavailable so caller can
        activate the Python web3.py fallback path.
        """
        if not self._client or not self._tx.healthy:
            return None
        payload = {"to": to, "value_eth": value_eth}
        if data:
            payload["data"] = data
        try:
            resp = await self._client.post(f"{TX_URL}/tx/send", json=payload)
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug("tx-engine send_eth failed: %s", exc)
            self._tx.healthy = False
        return None

    async def send_contract_call(self, contract: str, calldata: str, value_eth: float = 0.0) -> Optional[dict]:
        """Proxy ABI-encoded contract call to tx-engine or return None for fallback."""
        if not self._client or not self._tx.healthy:
            return None
        try:
            resp = await self._client.post(f"{TX_URL}/tx/contract", json={
                "contract": contract, "calldata": calldata, "value_eth": value_eth,
            })
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug("tx-engine contract call failed: %s", exc)
            self._tx.healthy = False
        return None

    # ── Status snapshot ───────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "dex_oracle": {
                "healthy":  self._dex.healthy,
                "running":  self._dex.running if hasattr(self._dex, "running") else self._dex.is_running(),
                "restarts": self._dex._restarts,
                "url":      DEX_URL,
                "binary":   str(DEX_BINARY),
                "available": self._dex._available,
            },
            "tx_engine": {
                "healthy":  self._tx.healthy,
                "running":  self._tx.is_running(),
                "restarts": self._tx._restarts,
                "url":      TX_URL,
                "binary":   str(TX_BINARY),
                "available": self._tx._available,
            },
        }
