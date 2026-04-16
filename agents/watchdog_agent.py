"""
agents/watchdog_agent.py — Micro-level Agent Monitoring
========================================================

Watches individual trading agents for:
  - Stuck / unresponsive agents (no cycle progress for N intervals)
  - Error-state agents (status == ERROR)
  - Zombie agents (marked RUNNING but task is dead)

Self-healing actions:
  - Restart stuck agents
  - Reset error-state agents
  - Log and report all interventions

Architecture:
  AgentWatchdog
    ├── check_all()          → scan every registered agent
    ├── _check_agent()       → inspect one agent's health
    ├── _restart_agent()     → stop + start an agent
    └── report()             → last watchdog report

Usage:
  from agents.watchdog_agent import AgentWatchdog
  watchdog = AgentWatchdog()
  report = await watchdog.check_all()
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aureon.watchdog_agent")

# ── Configuration ─────────────────────────────────────────────────────────────

# An agent is "stuck" if cycle_count hasn't changed for this many check rounds
STUCK_THRESHOLD_ROUNDS = 3

# Max consecutive errors before forced restart
MAX_CONSECUTIVE_ERRORS = 5


class AgentWatchdog:
    """
    Micro-level agent health monitor.

    Tracks per-agent cycle counts across check rounds. If an agent's
    cycle count doesn't advance for STUCK_THRESHOLD_ROUNDS, it is
    restarted automatically.
    """

    def __init__(self) -> None:
        # agent_id → {"last_cycle": int, "stale_rounds": int}
        self._tracking: Dict[str, Dict[str, Any]] = {}
        self._last_report: Optional[Dict[str, Any]] = None

    async def check_all(self) -> Dict[str, Any]:
        """
        Scan all registered agents and take corrective action.

        Returns a report dict with keys:
          - checked:   number of agents inspected
          - healthy:   list of healthy agent IDs
          - restarted: list of agent IDs that were restarted
          - errors:    list of agent IDs in error state
          - timestamp: ISO timestamp of the check
        """
        from intelligence.trading_agent import registry

        agents = registry.list_all()
        healthy: List[str] = []
        restarted: List[str] = []
        errors: List[str] = []

        for agent_info in agents:
            agent_id = agent_info.get("agent_id", "")
            agent = registry.get(agent_id)
            if not agent:
                continue

            action = await self._check_agent(agent_id, agent)
            if action == "healthy":
                healthy.append(agent_id)
            elif action == "restarted":
                restarted.append(agent_id)
            elif action == "error":
                errors.append(agent_id)

        # Clean up tracking for agents that no longer exist
        active_ids = {a.get("agent_id") for a in agents}
        stale_ids = [k for k in self._tracking if k not in active_ids]
        for sid in stale_ids:
            del self._tracking[sid]

        from datetime import datetime, timezone

        self._last_report = {
            "checked": len(agents),
            "healthy": healthy,
            "restarted": restarted,
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self._last_report

    async def _check_agent(self, agent_id: str, agent: Any) -> str:
        """
        Inspect a single agent's health.

        Returns: "healthy", "restarted", or "error"
        """
        from intelligence.trading_agent import AgentStatus

        status = agent.status
        cycle_count = agent.cycle_count

        # ── Error state → attempt restart ─────────────────────────────────────
        if status == AgentStatus.ERROR:
            logger.warning(
                "Agent %s in ERROR state — attempting restart", agent_id
            )
            success = await self._restart_agent(agent_id)
            return "restarted" if success else "error"

        # ── Not running → skip (it's idle or stopped intentionally) ───────────
        if status != AgentStatus.RUNNING:
            # Remove from tracking — not our concern
            self._tracking.pop(agent_id, None)
            return "healthy"

        # ── Running → check for stuck cycles ──────────────────────────────────
        tracking = self._tracking.get(agent_id)
        if tracking is None:
            self._tracking[agent_id] = {
                "last_cycle": cycle_count,
                "stale_rounds": 0,
            }
            return "healthy"

        if cycle_count == tracking["last_cycle"]:
            tracking["stale_rounds"] += 1
            if tracking["stale_rounds"] >= STUCK_THRESHOLD_ROUNDS:
                logger.warning(
                    "Agent %s stuck at cycle %d for %d rounds — restarting",
                    agent_id,
                    cycle_count,
                    tracking["stale_rounds"],
                )
                success = await self._restart_agent(agent_id)
                tracking["stale_rounds"] = 0
                tracking["last_cycle"] = 0
                return "restarted" if success else "error"
        else:
            tracking["last_cycle"] = cycle_count
            tracking["stale_rounds"] = 0

        return "healthy"

    async def _restart_agent(self, agent_id: str) -> bool:
        """Stop and restart an agent. Returns True on success."""
        from intelligence.trading_agent import registry

        try:
            agent = registry.get(agent_id)
            if agent is None:
                return False

            await agent.stop()
            await agent.start()
            logger.info("Agent %s restarted successfully", agent_id)
            return True
        except Exception as exc:
            logger.error("Failed to restart agent %s: %s", agent_id, exc)
            return False

    def report(self) -> Optional[Dict[str, Any]]:
        """Return the last watchdog report, or None if no check has run."""
        return self._last_report
