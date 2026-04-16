"""
watchdog/kernel.py — Central WatchdogKernel.

The kernel is the top-level coordinator for the entire watchdog system:

  1. Instantiates and registers all agents (file, process, service, trade, db, resource)
  2. Starts the EventBus dispatch loop
  3. Starts every registered agent's poll loop (one asyncio Task each)
  4. Subscribes to CRITICAL events and invokes the HealingStrategy gate-keeper
  5. Delegates actual healing to the emitting agent's heal() method
  6. Exposes a health_snapshot() API consumed by the dashboard

Lifecycle
---------
  kernel = WatchdogKernel()
  await kernel.start()          # non-blocking — returns immediately
  ...
  await kernel.stop()           # graceful shutdown of all tasks
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional

from watchdog.agents.db_agent       import DatabaseAgent
from watchdog.agents.file_agent     import FileAgent
from watchdog.agents.process_agent  import ProcessAgent
from watchdog.agents.resource_agent import ResourceAgent
from watchdog.agents.service_agent  import ServiceAgent
from watchdog.agents.trade_agent    import TradeLoopAgent
from watchdog.event_bus             import EventBus, EventSeverity, EventType, WatchdogEvent, event_bus
from watchdog.healing.actions       import HealingStrategy, HealEscalation, healing_strategy
from watchdog.registry              import AgentRegistry, discover_python_files

logger = logging.getLogger("watchdog.kernel")

_REPO_ROOT = Path(__file__).parent.parent

# ── Sidecar binary paths ──────────────────────────────────────────────────────
_DEX_ORACLE_BIN = _REPO_ROOT / "dex-oracle"  / "target" / "release" / "dex-oracle"
_TX_ENGINE_BIN  = _REPO_ROOT / "tx-engine"   / "target" / "release" / "tx-engine"

# ── DB paths ──────────────────────────────────────────────────────────────────
_DB_PATHS = [
    _REPO_ROOT / "aureon_memory.db",
    _REPO_ROOT / "aureon_persistence.db",
]


class WatchdogKernel:
    """
    Coordinates the full watchdog legion.

    Parameters
    ----------
    bus            : EventBus instance (defaults to module-level singleton)
    strategy       : HealingStrategy gate-keeper (defaults to module singleton)
    file_interval  : poll interval in seconds for FileAgents
    svc_interval   : poll interval for ServiceAgents
    db_interval    : poll interval for DatabaseAgents
    res_interval   : poll interval for ResourceAgent
    """

    def __init__(
        self,
        bus:           EventBus        = event_bus,
        strategy:      HealingStrategy = healing_strategy,
        file_interval: float           = 15.0,
        svc_interval:  float           = 10.0,
        db_interval:   float           = 60.0,
        res_interval:  float           = 30.0,
    ) -> None:
        self.bus      = bus
        self.strategy = strategy
        self.registry = AgentRegistry()
        self._started = False
        self._file_interval = file_interval
        self._svc_interval  = svc_interval
        self._db_interval   = db_interval
        self._res_interval  = res_interval
        self._critical_count = 0
        self._heal_count     = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Build the full agent set, start the event bus, and launch all
        agent poll loops.  Returns immediately (all work runs in background tasks).
        """
        if self._started:
            logger.warning("WatchdogKernel.start() called twice — ignoring")
            return

        logger.info("WatchdogKernel starting …")
        self._register_all_agents()
        self.bus.subscribe(self._on_event)       # all events
        await self.bus.start()

        agent_count = 0
        for agent in self.registry:
            await agent.start()
            agent_count += 1

        self._started = True
        logger.info("WatchdogKernel started: %d agents active", agent_count)

    async def stop(self) -> None:
        """Gracefully stop all agents and the event bus."""
        if not self._started:
            return
        logger.info("WatchdogKernel stopping …")
        for agent in self.registry:
            await agent.stop()
        await self.bus.stop()
        self._started = False
        logger.info(
            "WatchdogKernel stopped — criticals=%d heals=%d",
            self._critical_count, self._heal_count,
        )

    # ── Event handler ─────────────────────────────────────────────────────────

    async def _on_event(self, event: WatchdogEvent) -> None:
        """
        Route every event through the kernel.

        CRITICAL events are evaluated by HealingStrategy; if approved,
        the emitting agent's heal() is invoked.
        """
        if event.severity == EventSeverity.CRITICAL:
            self._critical_count += 1
            action = self.strategy.evaluate(event)

            if action.escalation == HealEscalation.GIVE_UP:
                logger.critical(
                    "GIVE UP on %s — %s: %s",
                    event.agent_id, event.event_type.name, action.reason,
                )
                return

            if action.escalation == HealEscalation.WAIT:
                logger.debug("Cool-down for %s: %s", event.agent_id, action.reason)
                return

            # Attempt heal via the owning agent
            agent = self.registry.get(event.agent_id)
            if agent is None:
                logger.error("No agent found for id=%s — cannot heal", event.agent_id)
                return

            logger.warning(
                "Healing %s (attempt #%d): %s",
                event.agent_id, action.attempt_no, event.message,
            )
            try:
                success = await agent.heal(event)
            except Exception as exc:
                logger.error("heal() raised for %s: %s", event.agent_id, exc)
                success = False

            self.strategy.record_result(event.agent_id, success)
            if success:
                self._heal_count += 1
            status_str = "SUCCESS" if success else "FAILED"
            logger.warning("Heal %s for %s", status_str, event.agent_id)

    # ── Health snapshot ───────────────────────────────────────────────────────

    def health_snapshot(self) -> dict:
        """
        Return a JSON-safe dict summarising the entire watchdog state.
        Consumed by the dashboard FastAPI endpoint.
        """
        return {
            "started":          self._started,
            "total_agents":     len(self.registry),
            "critical_events":  self._critical_count,
            "heals_performed":  self._heal_count,
            "bus_stats":        self.bus.stats,
            "severity_counts":  self.registry.counts_by_severity(),
            "heal_records":     self.strategy.snapshot(),
            "agents":           self.registry.health_snapshot(),
        }

    # ── Agent construction ────────────────────────────────────────────────────

    def _register_all_agents(self) -> None:
        """Build and register every agent type."""
        self._register_file_agents()
        self._register_process_agents()
        self._register_service_agents()
        self._register_trade_agent()
        self._register_db_agents()
        self._register_resource_agent()
        logger.info("Registry complete: %d total agents", len(self.registry))

    def _register_file_agents(self) -> None:
        files = discover_python_files(_REPO_ROOT)
        for path in files:
            agent = FileAgent(
                path      = path,
                repo_root = _REPO_ROOT,
                bus       = self.bus,
                interval  = self._file_interval,
            )
            self.registry.register(agent)
        logger.info("Registered %d FileAgents", len(files))

    def _register_process_agents(self) -> None:
        dex_port = int(os.getenv("DEX_ORACLE_PORT", "9001"))
        tx_port  = int(os.getenv("TX_ENGINE_PORT",  "9002"))

        sidecars = [
            {
                "name":       "dex-oracle",
                "binary":     _DEX_ORACLE_BIN,
                "health_url": f"http://127.0.0.1:{dex_port}/health",
                "env":        {"DEX_ORACLE_PORT": str(dex_port)},
            },
            {
                "name":       "tx-engine",
                "binary":     _TX_ENGINE_BIN,
                "health_url": f"http://127.0.0.1:{tx_port}/health",
                "env":        {"TX_ENGINE_PORT": str(tx_port)},
            },
        ]
        for cfg in sidecars:
            agent = ProcessAgent(
                name       = cfg["name"],
                binary     = cfg["binary"],
                health_url = cfg["health_url"],
                env        = cfg["env"],
                bus        = self.bus,
                interval   = 5.0,
            )
            self.registry.register(agent)
        logger.info("Registered %d ProcessAgents", len(sidecars))

    def _register_service_agents(self) -> None:
        services = [
            {
                "name":         "aureon-api",
                "host":         "127.0.0.1",
                "port":         8000,
                "entry_module": "main:app",
                "health_path":  "/health",
            },
            {
                "name":         "aureon-server",
                "host":         "127.0.0.1",
                "port":         8010,
                "entry_module": "aureon_server:app",
                "health_path":  "/health",
            },
        ]
        for cfg in services:
            agent = ServiceAgent(
                name         = cfg["name"],
                host         = cfg["host"],
                port         = cfg["port"],
                entry_module = cfg["entry_module"],
                health_path  = cfg["health_path"],
                bus          = self.bus,
                interval     = self._svc_interval,
                auto_restart = True,
            )
            self.registry.register(agent)
        logger.info("Registered %d ServiceAgents", len(services))

    def _register_trade_agent(self) -> None:
        agent = TradeLoopAgent(
            bus       = self.bus,
            interval  = 20.0,
            auto_heal = True,
        )
        self.registry.register(agent)
        logger.info("Registered TradeLoopAgent")

    def _register_db_agents(self) -> None:
        count = 0
        for db_path in _DB_PATHS:
            agent = DatabaseAgent(
                db_path  = db_path,
                bus      = self.bus,
                interval = self._db_interval,
            )
            self.registry.register(agent)
            count += 1
        logger.info("Registered %d DatabaseAgents", count)

    def _register_resource_agent(self) -> None:
        agent = ResourceAgent(
            bus      = self.bus,
            interval = self._res_interval,
        )
        self.registry.register(agent)
        logger.info("Registered ResourceAgent")


# ── Module-level singleton ────────────────────────────────────────────────────
kernel = WatchdogKernel()
