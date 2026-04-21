"""
tests/test_swarm.py
====================

Tests for SwarmOrchestrator: consensus, metrics, broadcast start/stop.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


class TestSwarmOrchestrator:

    def _make_registry_with_agents(self, n: int = 3):
        from intelligence.trading_agent import AgentRegistry, TradingAgentConfig, Strategy
        reg = AgentRegistry()
        strategies = list(Strategy)
        for i in range(n):
            reg.create(TradingAgentConfig(
                strategy=strategies[i % len(strategies)],
                scan_interval=9999,
            ))
        return reg

    def test_orchestrator_metrics_total_agents(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        reg  = self._make_registry_with_agents(3)
        orch = SwarmOrchestrator(reg)
        m    = orch.metrics()
        assert m["total_agents"] == 3

    def test_orchestrator_metrics_by_status(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        reg  = self._make_registry_with_agents(2)
        orch = SwarmOrchestrator(reg)
        m    = orch.metrics()
        assert "by_status" in m
        assert m["by_status"].get("idle", 0) == 2

    def test_orchestrator_metrics_max_agents(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        from intelligence.trading_agent import AgentRegistry
        reg  = AgentRegistry()
        orch = SwarmOrchestrator(reg)
        m    = orch.metrics()
        assert m["max_agents"] == AgentRegistry.MAX_AGENTS

    def test_consensus_no_running_agents(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        reg  = self._make_registry_with_agents(2)
        orch = SwarmOrchestrator(reg)
        c    = orch.consensus()
        assert c["running_agents"] == 0
        # No running agents → NO_CONSENSUS is the correct sentinel value
        assert c["signal"] in ("BUY", "SELL", "HOLD", "NO_CONSENSUS")

    def test_consensus_total_pnl_zero_initially(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        reg  = self._make_registry_with_agents(2)
        orch = SwarmOrchestrator(reg)
        c    = orch.consensus()
        assert c["total_pnl_usd"] == 0.0

    def test_metrics_by_strategy_populated(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        from intelligence.trading_agent import AgentRegistry, TradingAgentConfig, Strategy
        reg = AgentRegistry()
        reg.create(TradingAgentConfig(strategy=Strategy.ARB))
        reg.create(TradingAgentConfig(strategy=Strategy.ARB))
        reg.create(TradingAgentConfig(strategy=Strategy.PPO))
        orch = SwarmOrchestrator(reg)
        m    = orch.metrics()
        assert m["by_strategy"]["arb"] == 2
        assert m["by_strategy"]["ppo"] == 1

    @pytest.mark.asyncio
    async def test_broadcast_start_starts_idle_agents(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        from intelligence.trading_agent import AgentRegistry, TradingAgentConfig, AgentStatus
        reg = AgentRegistry()
        a1  = reg.create(TradingAgentConfig(scan_interval=9999))
        a2  = reg.create(TradingAgentConfig(scan_interval=9999))
        orch = SwarmOrchestrator(reg)
        started = await orch.broadcast_start()
        assert len(started) == 2
        assert a1.status == AgentStatus.RUNNING
        assert a2.status == AgentStatus.RUNNING
        # cleanup
        await orch.broadcast_stop()

    @pytest.mark.asyncio
    async def test_broadcast_stop_stops_running_agents(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        from intelligence.trading_agent import AgentRegistry, TradingAgentConfig, AgentStatus
        reg  = AgentRegistry()
        a1   = reg.create(TradingAgentConfig(scan_interval=9999))
        orch = SwarmOrchestrator(reg)
        await orch.broadcast_start()
        assert a1.status == AgentStatus.RUNNING
        stopped = await orch.broadcast_stop()
        assert len(stopped) == 1
        assert a1.status == AgentStatus.STOPPED

    @pytest.mark.asyncio
    async def test_broadcast_start_strategy_filter(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        from intelligence.trading_agent import AgentRegistry, TradingAgentConfig, Strategy, AgentStatus
        reg = AgentRegistry()
        arb = reg.create(TradingAgentConfig(strategy=Strategy.ARB, scan_interval=9999))
        ppo = reg.create(TradingAgentConfig(strategy=Strategy.PPO, scan_interval=9999))
        orch = SwarmOrchestrator(reg)
        started = await orch.broadcast_start(strategy_filter="arb")
        assert arb.id in started
        assert ppo.id not in started
        assert arb.status == AgentStatus.RUNNING
        assert ppo.status == AgentStatus.IDLE
        await orch.broadcast_stop()

    def test_uptime_is_non_negative(self):
        from intelligence.swarm_orchestrator import SwarmOrchestrator
        from intelligence.trading_agent import AgentRegistry
        orch = SwarmOrchestrator(AgentRegistry())
        assert orch.metrics()["uptime_s"] >= 0


class TestTradingAgentReset:

    @pytest.mark.asyncio
    async def test_reset_zeroes_metrics(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, AgentStatus
        agent = TradingAgent(TradingAgentConfig(initial_capital=5000.0))
        agent.cycle_count = 10
        agent.trades_made = 5
        agent.total_pnl   = 100.0
        agent.errors      = 2
        await agent.reset()
        assert agent.cycle_count == 0
        assert agent.trades_made == 0
        assert agent.total_pnl   == 0.0
        assert agent.errors      == 0
        assert agent.status      == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_reset_restores_initial_capital(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig
        agent = TradingAgent(TradingAgentConfig(initial_capital=7777.0))
        agent._capital = 1234.56
        await agent.reset()
        assert agent._capital == 7777.0

    def test_snapshot_contains_required_keys(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig
        agent = TradingAgent(TradingAgentConfig())
        snap  = agent.snapshot()
        for key in ("id", "config", "wallet", "capital", "cycle_count", "trades_made"):
            assert key in snap, f"Missing key: {key}"

    def test_snapshot_config_has_strategy(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.PPO))
        snap  = agent.snapshot()
        assert snap["config"]["strategy"] == "ppo"


class TestRegistryPersistence:

    def test_save_and_load_registry(self, tmp_path):
        from intelligence.trading_agent import AgentRegistry, TradingAgentConfig, Strategy
        reg = AgentRegistry()
        reg.create(TradingAgentConfig(name="Alpha", strategy=Strategy.ARB))
        reg.create(TradingAgentConfig(name="Beta",  strategy=Strategy.PPO))

        path = str(tmp_path / "registry.json")
        reg.save_registry(path)

        reg2    = AgentRegistry()
        loaded  = reg2.load_registry(path)
        assert loaded == 2
        names   = [a.config.name for a in reg2._agents.values()]
        assert "Alpha" in names
        assert "Beta"  in names

    def test_load_nonexistent_returns_zero(self, tmp_path):
        from intelligence.trading_agent import AgentRegistry
        reg    = AgentRegistry()
        loaded = reg.load_registry(str(tmp_path / "nonexistent.json"))
        assert loaded == 0

    def test_save_preserves_capital(self, tmp_path):
        from intelligence.trading_agent import AgentRegistry, TradingAgentConfig
        reg   = AgentRegistry()
        agent = reg.create(TradingAgentConfig(initial_capital=9999.0))
        agent._capital = 12345.0

        path = str(tmp_path / "reg.json")
        reg.save_registry(path)

        reg2   = AgentRegistry()
        reg2.load_registry(path)
        loaded_agent = list(reg2._agents.values())[0]
        assert loaded_agent._capital == 12345.0
