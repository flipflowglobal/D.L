"""
tests/test_multi_agent.py
==========================

Offline tests for the multi-agent trading system:
  - TradingAgent wallet generation
  - TradingAgentConfig defaults and validation
  - AgentRegistry: create / list / start / stop / performance
  - Strategy-specific algorithm instantiation
  - All five strategy cycle handlers (dry-run / simulated)
  - FastAPI multi-agent API endpoints: POST /agents, GET /agents,
    POST /agents/{id}/start, POST /agents/{id}/stop,
    GET /agents/{id}/performance, DELETE /agents/{id},
    POST /wallet/generate
"""

from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Wallet generation
# ─────────────────────────────────────────────────────────────────────────────

class TestWalletGeneration:

    def test_generate_wallet_returns_address_and_key(self):
        from intelligence.trading_agent import generate_wallet
        w = generate_wallet()
        assert "address" in w
        assert "private_key" in w
        assert w["address"].startswith("0x")
        assert len(w["address"]) == 42

    def test_generated_private_key_is_hex(self):
        from intelligence.trading_agent import generate_wallet
        w = generate_wallet()
        key = w["private_key"]
        # eth_account .key.hex() returns 64-char hex string (no 0x prefix)
        stripped = key[2:] if key.startswith("0x") else key
        assert len(stripped) == 64
        int(stripped, 16)   # must be valid hex

    def test_two_wallets_are_different(self):
        from intelligence.trading_agent import generate_wallet
        w1 = generate_wallet()
        w2 = generate_wallet()
        assert w1["address"] != w2["address"]
        assert w1["private_key"] != w2["private_key"]

    def test_agent_auto_generates_wallet(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig
        agent = TradingAgent(TradingAgentConfig())
        assert agent.wallet["address"].startswith("0x")
        assert len(agent.wallet["address"]) == 42

    def test_agent_accepts_supplied_private_key(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig
        # well-known Hardhat test key
        key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        config = TradingAgentConfig(private_key=key)
        agent  = TradingAgent(config)
        assert agent.wallet["address"] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


# ─────────────────────────────────────────────────────────────────────────────
# TradingAgentConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestTradingAgentConfig:

    def test_defaults(self):
        from intelligence.trading_agent import TradingAgentConfig, Strategy, Chain, Token
        cfg = TradingAgentConfig()
        assert cfg.strategy == Strategy.ARB
        assert cfg.chain    == Chain.ETHEREUM
        assert cfg.token    == Token.ETH
        assert cfg.dry_run  is True

    def test_custom_values(self):
        from intelligence.trading_agent import TradingAgentConfig, Strategy, Chain, Token
        cfg = TradingAgentConfig(
            name="TestBot",
            strategy=Strategy.PPO,
            chain=Chain.ARBITRUM,
            token=Token.USDC,
            initial_capital=5000.0,
            dry_run=False,
        )
        assert cfg.name            == "TestBot"
        assert cfg.strategy        == Strategy.PPO
        assert cfg.chain           == Chain.ARBITRUM
        assert cfg.token           == Token.USDC
        assert cfg.initial_capital == 5000.0
        assert cfg.dry_run         is False

    def test_all_strategies_valid(self):
        from intelligence.trading_agent import TradingAgentConfig, Strategy
        for s in Strategy:
            cfg = TradingAgentConfig(strategy=s)
            assert cfg.strategy == s

    def test_all_chains_valid(self):
        from intelligence.trading_agent import TradingAgentConfig, Chain
        for c in Chain:
            cfg = TradingAgentConfig(chain=c)
            assert cfg.chain == c

    def test_all_tokens_valid(self):
        from intelligence.trading_agent import TradingAgentConfig, Token
        for t in Token:
            cfg = TradingAgentConfig(token=t)
            assert cfg.token == t


# ─────────────────────────────────────────────────────────────────────────────
# TradingAgent
# ─────────────────────────────────────────────────────────────────────────────

class TestTradingAgent:

    def test_initial_status_is_idle(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, AgentStatus
        agent = TradingAgent(TradingAgentConfig())
        assert agent.status == AgentStatus.IDLE

    def test_id_is_8_chars(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig
        agent = TradingAgent(TradingAgentConfig())
        assert len(agent.id) == 8

    def test_performance_dict_keys(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig
        agent = TradingAgent(TradingAgentConfig())
        perf  = agent.performance()
        for key in ("agent_id", "strategy", "chain", "token", "wallet_address",
                    "cycle_count", "trades_made", "capital_usd", "roi_pct"):
            assert key in perf

    def test_to_dict_keys(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig
        agent = TradingAgent(TradingAgentConfig())
        d     = agent.to_dict()
        for key in ("agent_id", "name", "strategy", "chain", "token",
                    "status", "wallet_address", "cycle_count"):
            assert key in d

    def test_arb_algorithm_instantiated(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.ARB))
        assert isinstance(agent._algorithm, BellmanFordArb)

    def test_ppo_algorithm_instantiated(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        from nexus_arb.algorithms.ppo import TradingPolicy
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.PPO))
        assert isinstance(agent._algorithm, TradingPolicy)

    def test_mean_reversion_algorithm_instantiated(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        from nexus_arb.algorithms.cma_es import CMAES
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.MEAN_REVERSION))
        assert isinstance(agent._algorithm, CMAES)

    def test_flash_loan_algorithm_instantiated(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.FLASH_LOAN))
        assert isinstance(agent._algorithm, ThompsonSamplingBandit)

    def test_adaptive_algorithm_instantiated(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.ADAPTIVE))
        assert isinstance(agent._algorithm, UnscentedKalmanFilter)

    def test_arb_cycle_returns_action_and_pnl(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.ARB))
        agent._prev_price = 2000.0
        action, pnl = agent._cycle_arb(2001.0)
        assert isinstance(action, str)
        assert isinstance(pnl, float)

    def test_ppo_cycle_returns_action_and_pnl(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.PPO))
        agent._prev_price = 2000.0
        action, pnl = agent._cycle_ppo(2001.0)
        assert isinstance(action, str)
        assert action in ("BUY", "SELL", "HOLD")

    def test_flash_loan_cycle_returns_action(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.FLASH_LOAN))
        agent._prev_price = 2000.0
        action, pnl = agent._cycle_flash_loan(2000.0)
        assert isinstance(action, str)
        assert isinstance(pnl, float)

    def test_adaptive_cycle_returns_action(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.ADAPTIVE))
        agent._prev_price = 2000.0
        action, pnl = agent._cycle_adaptive(2001.0)
        assert isinstance(action, str)
        assert isinstance(pnl, float)

    def test_mean_reversion_cycle_returns_action(self):
        from intelligence.trading_agent import TradingAgent, TradingAgentConfig, Strategy
        agent = TradingAgent(TradingAgentConfig(strategy=Strategy.MEAN_REVERSION))
        agent._prev_price = 2000.0
        # Feed some history so mean-reversion can compute
        for p in [2000.0] * 15:
            agent._cycle_mean_reversion(p)
        action, pnl = agent._cycle_mean_reversion(2000.0)
        assert isinstance(action, str)


# ─────────────────────────────────────────────────────────────────────────────
# AgentRegistry
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentRegistry:

    def _fresh_registry(self):
        from intelligence.trading_agent import AgentRegistry
        return AgentRegistry()

    def test_create_returns_agent(self):
        from intelligence.trading_agent import TradingAgentConfig
        reg   = self._fresh_registry()
        agent = reg.create(TradingAgentConfig(name="Bot1"))
        assert agent.id in [a["agent_id"] for a in reg.list_all()]

    def test_list_all_returns_list(self):
        reg = self._fresh_registry()
        assert isinstance(reg.list_all(), list)

    def test_count_increments(self):
        from intelligence.trading_agent import TradingAgentConfig
        reg = self._fresh_registry()
        assert reg.count() == 0
        reg.create(TradingAgentConfig())
        assert reg.count() == 1
        reg.create(TradingAgentConfig())
        assert reg.count() == 2

    def test_get_returns_agent(self):
        from intelligence.trading_agent import TradingAgentConfig
        reg   = self._fresh_registry()
        agent = reg.create(TradingAgentConfig())
        assert reg.get(agent.id) is agent

    def test_get_unknown_returns_none(self):
        reg = self._fresh_registry()
        assert reg.get("nonexistent") is None

    def test_remove_deletes_agent(self):
        from intelligence.trading_agent import TradingAgentConfig
        reg   = self._fresh_registry()
        agent = reg.create(TradingAgentConfig())
        reg.remove(agent.id)
        assert reg.get(agent.id) is None
        assert reg.count() == 0

    def test_max_agents_limit(self):
        from intelligence.trading_agent import TradingAgentConfig, AgentRegistry
        reg = AgentRegistry()
        for _ in range(AgentRegistry.MAX_AGENTS):
            reg.create(TradingAgentConfig())
        with pytest.raises(RuntimeError, match="limit"):
            reg.create(TradingAgentConfig())

    @pytest.mark.asyncio
    async def test_start_changes_status_to_running(self):
        from intelligence.trading_agent import TradingAgentConfig, AgentStatus
        reg   = self._fresh_registry()
        agent = reg.create(TradingAgentConfig(scan_interval=9999))
        await reg.start(agent.id)
        assert agent.status == AgentStatus.RUNNING
        await reg.stop(agent.id)

    @pytest.mark.asyncio
    async def test_stop_changes_status_to_stopped(self):
        from intelligence.trading_agent import TradingAgentConfig, AgentStatus
        reg   = self._fresh_registry()
        agent = reg.create(TradingAgentConfig(scan_interval=9999))
        await reg.start(agent.id)
        await reg.stop(agent.id)
        assert agent.status == AgentStatus.STOPPED

    @pytest.mark.asyncio
    async def test_start_unknown_raises_key_error(self):
        reg = self._fresh_registry()
        with pytest.raises(KeyError):
            await reg.start("badid")

    def test_multiple_agents_independent_wallets(self):
        from intelligence.trading_agent import TradingAgentConfig
        reg = self._fresh_registry()
        agents = [reg.create(TradingAgentConfig()) for _ in range(5)]
        addresses = [a.wallet["address"] for a in agents]
        assert len(set(addresses)) == 5   # all unique


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI multi-agent API
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
async def client():
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def test_root_v2(client):
    r = await client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["system"] == "AUREON"
    assert "agents_active" in data


async def test_list_strategies(client):
    r = await client.get("/strategies")
    assert r.status_code == 200
    data = r.json()
    assert "strategies" in data
    for key in ("arb", "ppo", "mean_reversion", "flash_loan", "adaptive"):
        assert key in data["strategies"]


async def test_list_chains(client):
    r = await client.get("/chains")
    assert r.status_code == 200
    assert "chains" in r.json()


async def test_list_tokens(client):
    r = await client.get("/tokens")
    assert r.status_code == 200
    assert "tokens" in r.json()


async def test_create_agent_auto_wallet(client):
    r = await client.post("/agents", json={
        "name": "TestBot",
        "strategy": "arb",
        "chain": "ethereum",
        "token": "ETH",
        "dry_run": True,
    })
    assert r.status_code == 201
    data = r.json()
    assert "agent_id" in data
    assert "wallet_address" in data
    assert data["wallet_address"].startswith("0x")
    assert data["status"] == "idle"


async def test_create_agent_all_strategies(client):
    for strategy in ("arb", "ppo", "mean_reversion", "flash_loan", "adaptive"):
        r = await client.post("/agents", json={"strategy": strategy, "dry_run": True})
        assert r.status_code == 201, f"Failed for strategy={strategy}: {r.text}"


async def test_get_agent_not_found(client):
    r = await client.get("/agents/nonexistent")
    assert r.status_code == 404


async def test_list_agents_returns_count(client):
    r = await client.get("/agents")
    assert r.status_code == 200
    data = r.json()
    assert "count" in data
    assert "agents" in data
    assert isinstance(data["agents"], list)


async def test_agent_performance_endpoint(client):
    create = await client.post("/agents", json={"strategy": "ppo", "dry_run": True})
    agent_id = create.json()["agent_id"]

    r = await client.get(f"/agents/{agent_id}/performance")
    assert r.status_code == 200
    data = r.json()
    assert data["agent_id"] == agent_id
    assert "roi_pct" in data
    assert "total_pnl_usd" in data
    assert "wallet_address" in data


async def test_start_stop_agent(client):
    create = await client.post("/agents", json={"strategy": "arb", "dry_run": True})
    agent_id = create.json()["agent_id"]

    start = await client.post(f"/agents/{agent_id}/start")
    assert start.status_code == 200
    assert start.json()["status"] == "running"

    stop = await client.post(f"/agents/{agent_id}/stop")
    assert stop.status_code == 200
    assert stop.json()["status"] == "stopped"


async def test_delete_agent(client):
    create = await client.post("/agents", json={"strategy": "arb", "dry_run": True})
    agent_id = create.json()["agent_id"]

    delete = await client.delete(f"/agents/{agent_id}")
    assert delete.status_code == 204

    get = await client.get(f"/agents/{agent_id}")
    assert get.status_code == 404


async def test_generate_wallet_endpoint(client):
    r = await client.post("/wallet/generate")
    assert r.status_code == 200
    data = r.json()
    assert "address" in data
    assert "private_key" in data
    assert data["address"].startswith("0x")
    assert "warning" in data


async def test_status_includes_strategies(client):
    r = await client.get("/status")
    assert r.status_code == 200
    data = r.json()
    assert "strategies" in data
    assert "arb" in data["strategies"]
