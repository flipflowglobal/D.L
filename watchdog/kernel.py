"""
watchdog/kernel.py — Central WatchdogKernel.

The kernel is the top-level coordinator for the entire watchdog system:

  1. Instantiates and registers all agents (file, process, service, trade, db, resource)
  2. Connects every agent to the SharedMind (each receives a SyncBridge)
  3. Starts the EventBus dispatch loop
  4. Starts every registered agent's poll loop (one asyncio Task each)
  5. Subscribes to all CRITICAL events — routes them through:
       a) HealingStrategy gate-keeper (cool-down / max-attempts)
       b) SharedMind consensus  (conflict guard / quorum voting)
       c) Agent.heal()           (actual self-healing)
  6. Exposes health_snapshot() consumed by the dashboard

Lifecycle
---------
  kernel = WatchdogKernel()
  await kernel.start()     # non-blocking — all work runs as background tasks
  ...
  await kernel.stop()      # graceful shutdown
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
from watchdog.event_bus             import (
    EventBus, EventSeverity, EventType, WatchdogEvent, event_bus,
)
from watchdog.healing.actions       import HealEscalation, HealingStrategy, healing_strategy
from watchdog.mind.sync             import SharedMind, shared_mind
from watchdog.registry              import AgentRegistry, discover_python_files

logger = logging.getLogger("watchdog.kernel")

_REPO_ROOT = Path(__file__).parent.parent

# ── Sidecar binary paths ──────────────────────────────────────────────────────
_DEX_ORACLE_BIN = _REPO_ROOT / "dex-oracle" / "target" / "release" / "dex-oracle"
_TX_ENGINE_BIN  = _REPO_ROOT / "tx-engine"  / "target" / "release" / "tx-engine"

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
    bus            : EventBus instance (defaults to module singleton)
    strategy       : HealingStrategy gate-keeper (defaults to module singleton)
    mind           : SharedMind façade (defaults to module singleton)
    file_interval  : FileAgent poll interval (seconds)
    svc_interval   : ServiceAgent poll interval
    db_interval    : DatabaseAgent poll interval
    res_interval   : ResourceAgent poll interval
    """

    def __init__(
        self,
        bus:           EventBus        = event_bus,
        strategy:      HealingStrategy = healing_strategy,
        mind:          SharedMind      = shared_mind,
        file_interval: float           = 15.0,
        svc_interval:  float           = 10.0,
        db_interval:   float           = 60.0,
        res_interval:  float           = 30.0,
    ) -> None:
        self.bus      = bus
        self.strategy = strategy
        self.mind     = mind
        self.registry = AgentRegistry()
        self._started = False

        self._file_interval = file_interval
        self._svc_interval  = svc_interval
        self._db_interval   = db_interval
        self._res_interval  = res_interval

        self._critical_count  = 0
        self._heal_count      = 0
        self._consensus_rejects = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Build the full agent set, wire each agent to the shared mind,
        start the event bus, and launch all poll loops.
        Returns immediately (all work runs in background tasks).
        """
        if self._started:
            logger.warning("WatchdogKernel.start() called twice — ignoring")
            return

        logger.info("WatchdogKernel starting …")
        self._register_all_agents()
        self._wire_mind()
        self.bus.subscribe(self._on_event)
        await self.bus.start()

        agent_count = 0
        for agent in self.registry:
            await agent.start()
            agent_count += 1

        self._started = True
        logger.info(
            "WatchdogKernel started: %d agents active, SharedMind online",
            agent_count,
        )

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
            "WatchdogKernel stopped — criticals=%d heals=%d consensus_rejects=%d",
            self._critical_count, self._heal_count, self._consensus_rejects,
        )

    # ── Shared-mind wiring ────────────────────────────────────────────────────

    def _wire_mind(self) -> None:
        """Connect every registered agent to the shared mind."""
        for agent in self.registry:
            agent.mind = self.mind.connect(agent.agent_id)
        logger.info(
            "SharedMind wired: %d agents connected (%d shards)",
            len(self.registry), len(self.registry),
        )

    # ── Event handler ─────────────────────────────────────────────────────────

    async def _on_event(self, event: WatchdogEvent) -> None:
        """
        Route every event through the kernel.

        For CRITICAL events:
          1. HealingStrategy gate (cool-down / max attempts)
          2. SharedMind consensus (conflict guard / quorum)
          3. Agent.heal()
        """
        if event.severity != EventSeverity.CRITICAL:
            return

        self._critical_count += 1

        # ── Gate 1: HealingStrategy ───────────────────────────────────────────
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

        # ── Gate 2: SharedMind consensus ──────────────────────────────────────
        consensus = await self.mind.propose_heal(event)
        if not consensus.approved:
            self._consensus_rejects += 1
            logger.warning(
                "Consensus REJECTED heal for %s: %s (YES=%d NO=%d)",
                event.agent_id, consensus.reason,
                consensus.yes_count, consensus.no_count,
            )
            return

        logger.debug(
            "Consensus APPROVED heal for %s: %s",
            event.agent_id, consensus.reason,
        )

        # ── Gate 3: Agent.heal() ──────────────────────────────────────────────
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
        logger.warning(
            "Heal %s for %s", "SUCCESS" if success else "FAILED", event.agent_id,
        )

    # ── Health snapshot ───────────────────────────────────────────────────────

    def health_snapshot(self) -> dict:
        """
        JSON-safe dict summarising the entire watchdog state.
        Consumed by the dashboard FastAPI endpoint.
        """
        return {
            "started":             self._started,
            "total_agents":        len(self.registry),
            "critical_events":     self._critical_count,
            "heals_performed":     self._heal_count,
            "consensus_rejects":   self._consensus_rejects,
            "bus_stats":           self.bus.stats,
            "severity_counts":     self.registry.counts_by_severity(),
            "heal_records":        self.strategy.snapshot(),
            "mind":                self.mind.global_snapshot(),
            "agents":              self.registry.health_snapshot(),
        }

    # ── Agent construction ────────────────────────────────────────────────────

    def _register_all_agents(self) -> None:
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
        for db_path in _DB_PATHS:
            agent = DatabaseAgent(
                db_path  = db_path,
                bus      = self.bus,
                interval = self._db_interval,
            )
            self.registry.register(agent)
        logger.info("Registered %d DatabaseAgents", len(_DB_PATHS))

    def _register_resource_agent(self) -> None:
        agent = ResourceAgent(
            bus      = self.bus,
            interval = self._res_interval,
        )
        self.registry.register(agent)
        logger.info("Registered ResourceAgent")


# ── Module-level singleton ────────────────────────────────────────────────────
kernel = WatchdogKernel()
