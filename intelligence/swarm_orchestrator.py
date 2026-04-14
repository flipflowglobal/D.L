"""
intelligence/swarm_orchestrator.py
====================================

SwarmOrchestrator — coordinates multiple TradingAgent instances.

Responsibilities
----------------
- Aggregate trading signals from all running agents (majority vote / weighted ensemble)
- Detect conflicting strategies and emit warnings
- Provide swarm-wide metrics: total PnL, active agents, ensemble consensus
- Run a background health-checker that marks agents ERROR if they exceed error thresholds
- Allow broadcasting a command (start/stop) to all agents matching a filter
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aureon.swarm")

_MAX_ERRORS_BEFORE_FLAG = 5
_HEALTH_CHECK_INTERVAL  = 60  # seconds


class SwarmOrchestrator:
    """
    Coordinates a collection of TradingAgent instances.

    Usage
    -----
    swarm = SwarmOrchestrator(registry)
    await swarm.start()           # starts background health-checker
    consensus = swarm.consensus() # aggregated signal
    await swarm.stop()            # stops health-checker
    """

    def __init__(self, registry) -> None:
        self._registry  = registry
        self._task: Optional[asyncio.Task] = None
        self._running   = False
        self._started   = time.time()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start background health-checking loop."""
        self._running = True
        self._task    = asyncio.create_task(
            self._health_loop(), name="swarm-health"
        )
        logger.info("SwarmOrchestrator started — monitoring %d agents", self._registry.count())

    async def stop(self) -> None:
        """Stop the orchestrator."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SwarmOrchestrator stopped")

    # ── Health checker ────────────────────────────────────────────────────────

    async def _health_loop(self) -> None:
        from intelligence.trading_agent import AgentStatus
        while self._running:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
            for agent in self._registry._agents.values():
                if agent.errors >= _MAX_ERRORS_BEFORE_FLAG:
                    if agent.status == AgentStatus.RUNNING:
                        logger.warning(
                            "Agent %s exceeded error threshold (%d errors) — flagging ERROR",
                            agent.id, agent.errors,
                        )
                        agent.status = AgentStatus.ERROR

    # ── Consensus signal ──────────────────────────────────────────────────────

    def consensus(self) -> Dict[str, Any]:
        """
        Aggregate last-cycle actions from all RUNNING agents.

        Returns a majority-vote signal: BUY / SELL / HOLD along with
        vote counts and total swarm PnL.
        """
        from intelligence.trading_agent import AgentStatus
        running = [
            a for a in self._registry._agents.values()
            if a.status == AgentStatus.RUNNING
        ]

        votes: Dict[str, int] = {"BUY": 0, "SELL": 0, "HOLD": 0}
        total_pnl   = 0.0
        total_trades = 0

        for agent in running:
            last   = agent._last_result
            action = (last.get("action") or "HOLD").upper()
            if "BUY"  in action: votes["BUY"]  += 1
            elif "SELL" in action: votes["SELL"] += 1
            else:                 votes["HOLD"]  += 1
            total_pnl    += agent.total_pnl
            total_trades += agent.trades_made

        majority = max(votes, key=votes.__getitem__)
        if not running:
            majority = "NO_CONSENSUS"
        return {
            "signal":        majority,
            "votes":         votes,
            "running_agents": len(running),
            "total_pnl_usd": round(total_pnl, 2),
            "total_trades":  total_trades,
            "uptime_s":      round(time.time() - self._started, 1),
        }

    # ── Broadcast commands ────────────────────────────────────────────────────

    async def broadcast_start(
        self,
        strategy_filter: Optional[str] = None,
    ) -> List[str]:
        """Start all (optionally filtered) idle agents. Returns list of started IDs."""
        from intelligence.trading_agent import AgentStatus
        started = []
        for agent in list(self._registry._agents.values()):
            if agent.status != AgentStatus.IDLE:
                continue
            if strategy_filter and agent.config.strategy.value != strategy_filter:
                continue
            await agent.start()
            started.append(agent.id)
        return started

    async def broadcast_stop(
        self,
        strategy_filter: Optional[str] = None,
    ) -> List[str]:
        """Stop all (optionally filtered) running agents. Returns list of stopped IDs."""
        from intelligence.trading_agent import AgentStatus
        stopped = []
        for agent in list(self._registry._agents.values()):
            if agent.status != AgentStatus.RUNNING:
                continue
            if strategy_filter and agent.config.strategy.value != strategy_filter:
                continue
            await agent.stop()
            stopped.append(agent.id)
        return stopped

    # ── Swarm metrics ─────────────────────────────────────────────────────────

    def metrics(self) -> Dict[str, Any]:
        """Return swarm-wide metrics."""
        from intelligence.trading_agent import AgentStatus
        agents = list(self._registry._agents.values())
        by_status = {}
        by_strategy = {}
        total_capital = 0.0
        total_pnl     = 0.0
        total_errors  = 0

        for a in agents:
            s = a.status.value
            by_status[s]   = by_status.get(s, 0) + 1
            st = a.config.strategy.value
            by_strategy[st] = by_strategy.get(st, 0) + 1
            total_capital   += a._capital
            total_pnl       += a.total_pnl
            total_errors    += a.errors

        return {
            "total_agents":    len(agents),
            "by_status":       by_status,
            "by_strategy":     by_strategy,
            "total_capital_usd": round(total_capital, 2),
            "total_pnl_usd":     round(total_pnl, 2),
            "total_errors":      total_errors,
            "uptime_s":          round(time.time() - self._started, 1),
            "max_agents":        self._registry.MAX_AGENTS,
        }


# ── Module-level singleton ────────────────────────────────────────────────────
# Imported lazily by main.py after registry is available

def build_orchestrator(registry) -> SwarmOrchestrator:
    """Factory — called once during app startup."""
    return SwarmOrchestrator(registry)
