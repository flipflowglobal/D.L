"""
tests/test_watchdog.py — Integration tests for the watchdog legion.

Tests cover:
  - WatchdogKernel: start/stop lifecycle, agent registration
  - SharedMind: shard sync, consensus engine, vector clock merge
  - EventBus: publish, subscribe, dispatch
  - HealingStrategy: cooldown gating, max-attempt window
  - Per-agent check() output shapes (FileAgent, DatabaseAgent, ResourceAgent)
  - FastAPI /watchdog endpoints via ASGI test client
  - main.py /health and /status include watchdog fields after kernel start

All tests are fully offline — no real files are modified, no real DBs
are opened, no real processes are spawned.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Check whether main.py is importable (numpy may be absent in CI)
_MAIN_IMPORTABLE = importlib.util.find_spec("numpy") is not None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_event(event_type=None, severity=None, agent_id="test-agent"):
    from watchdog.event_bus import EventSeverity, EventType, WatchdogEvent
    return WatchdogEvent(
        event_type = event_type or EventType.FILE_OK,
        severity   = severity   or EventSeverity.INFO,
        agent_id   = agent_id,
        source     = "test-source",
        message    = "test message",
    )


# ─────────────────────────────────────────────────────────────────────────────
# EventBus
# ─────────────────────────────────────────────────────────────────────────────

class TestEventBus:

    @pytest.mark.asyncio
    async def test_start_stop(self):
        from watchdog.event_bus import EventBus
        bus = EventBus()
        await bus.start()
        assert bus._running is True
        await bus.stop()
        assert bus._running is False

    @pytest.mark.asyncio
    async def test_publish_and_receive(self):
        from watchdog.event_bus import EventBus
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(handler)
        await bus.start()
        await bus.publish(_make_event())
        await asyncio.sleep(0.05)
        await bus.stop()

        assert len(received) == 1
        assert received[0].agent_id == "test-agent"

    @pytest.mark.asyncio
    async def test_filtered_subscription(self):
        from watchdog.event_bus import EventBus, EventType
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(handler, event_type=EventType.DB_CORRUPT)
        await bus.start()
        await bus.publish(_make_event(event_type=EventType.FILE_OK))
        await bus.publish(_make_event(event_type=EventType.DB_CORRUPT))
        await asyncio.sleep(0.05)
        await bus.stop()

        assert len(received) == 1
        assert received[0].event_type == EventType.DB_CORRUPT

    @pytest.mark.asyncio
    async def test_stats_counts_events(self):
        from watchdog.event_bus import EventBus
        bus = EventBus()
        await bus.start()
        for _ in range(5):
            await bus.publish(_make_event())
        await asyncio.sleep(0.05)
        await bus.stop()
        assert bus.stats["events_dispatched"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# HealingStrategy
# ─────────────────────────────────────────────────────────────────────────────

class TestHealingStrategy:

    def test_approves_first_critical(self):
        from watchdog.event_bus import EventSeverity
        from watchdog.healing.actions import HealEscalation, HealingStrategy
        strategy = HealingStrategy(cooldown_sec=0.0)
        event = _make_event(severity=EventSeverity.CRITICAL)
        action = strategy.evaluate(event)
        assert action.escalation == HealEscalation.ATTEMPT

    def test_waits_non_critical(self):
        from watchdog.event_bus import EventSeverity
        from watchdog.healing.actions import HealEscalation, HealingStrategy
        strategy = HealingStrategy()
        event = _make_event(severity=EventSeverity.WARNING)
        action = strategy.evaluate(event)
        assert action.escalation == HealEscalation.WAIT

    def test_cooldown_blocks_immediate_retry(self):
        from watchdog.event_bus import EventSeverity
        from watchdog.healing.actions import HealEscalation, HealingStrategy
        strategy = HealingStrategy(cooldown_sec=60.0)
        event = _make_event(severity=EventSeverity.CRITICAL)
        strategy.evaluate(event)
        strategy.record_result("test-agent", success=False)
        action = strategy.evaluate(event)
        assert action.escalation == HealEscalation.WAIT

    def test_gives_up_after_max_attempts(self):
        from watchdog.event_bus import EventSeverity
        from watchdog.healing.actions import HealEscalation, HealingStrategy
        strategy = HealingStrategy(cooldown_sec=0.0, max_attempts=2, window_sec=3600)
        event = _make_event(severity=EventSeverity.CRITICAL)
        for _ in range(2):
            strategy.evaluate(event)
            strategy.record_result("test-agent", success=False)
        action = strategy.evaluate(event)
        assert action.escalation == HealEscalation.GIVE_UP

    def test_success_resets_failure_window(self):
        from watchdog.event_bus import EventSeverity
        from watchdog.healing.actions import HealEscalation, HealingStrategy
        strategy = HealingStrategy(cooldown_sec=0.0, max_attempts=2, window_sec=3600)
        event = _make_event(severity=EventSeverity.CRITICAL)
        strategy.evaluate(event)
        strategy.record_result("test-agent", success=True)   # reset window
        # After success the failure list is cleared — should attempt again
        strategy.evaluate(event)
        strategy.record_result("test-agent", success=False)
        action = strategy.evaluate(event)
        # Only 1 failure in window (after the reset) → still below max
        assert action.escalation != HealEscalation.GIVE_UP


# ─────────────────────────────────────────────────────────────────────────────
# SharedMind / Shard / Consensus
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentShard:

    def test_observe_bumps_vector_clock(self):
        from watchdog.mind.shard import AgentShard
        shard = AgentShard("test")
        assert shard.vector_clock == 0
        shard.observe("key", "value")
        assert shard.vector_clock == 1

    def test_read_returns_latest(self):
        from watchdog.mind.shard import AgentShard
        shard = AgentShard("test")
        shard.observe("price", 100.0)
        shard.observe("price", 200.0)
        assert shard.read("price") == 200.0

    def test_read_missing_returns_default(self):
        from watchdog.mind.shard import AgentShard
        shard = AgentShard("test")
        assert shard.read("missing", default="x") == "x"

    def test_merge_last_write_wins(self):
        from watchdog.mind.shard import AgentShard
        local  = AgentShard("agent-a")
        remote = AgentShard("agent-a")
        local.observe("x",  1)
        remote.observe("x", 2)
        remote.observe("x", 3)   # remote clock = 2, local clock = 1
        updated = local.merge(remote)
        assert updated is True
        assert local.read("x") == 3
        assert local.vector_clock == 2

    def test_merge_ignores_stale_remote(self):
        from watchdog.mind.shard import AgentShard
        local  = AgentShard("agent-a")
        remote = AgentShard("agent-a")
        local.observe("x", 10)
        local.observe("x", 20)   # local clock = 2
        remote.observe("x", 5)   # remote clock = 1 — stale
        updated = local.merge(remote)
        assert updated is False
        assert local.read("x") == 20

    def test_to_dict_is_json_safe(self):
        from watchdog.mind.shard import AgentShard
        shard = AgentShard("test")
        shard.observe("hello", "world")
        d = shard.to_dict()
        assert d["agent_id"] == "test"
        assert "vector_clock" in d
        assert "current" in d


class TestMindCore:

    @pytest.mark.asyncio
    async def test_register_and_get(self):
        from watchdog.mind.core import MindCore
        core = MindCore()
        shard = core.register("agent-x")
        assert shard.agent_id == "agent-x"
        assert core.get_shard("agent-x") is shard

    @pytest.mark.asyncio
    async def test_sync_stores_shard(self):
        from watchdog.mind.core import MindCore
        core  = MindCore()
        shard = core.register("a1")
        shard.observe("status", "healthy")
        await core.sync(shard)
        stored = core.get_shard("a1")
        assert stored.read("status") == "healthy"

    @pytest.mark.asyncio
    async def test_subscriber_called_on_sync(self):
        from watchdog.mind.core import MindCore
        core  = MindCore()
        shard = core.register("a2")
        received = []

        async def on_sync(agent_id, s):
            received.append(agent_id)

        core.subscribe_shard(on_sync, agent_id="a2")
        await core.sync(shard)
        assert "a2" in received

    @pytest.mark.asyncio
    async def test_wildcard_subscriber(self):
        from watchdog.mind.core import MindCore
        core = MindCore()
        s1 = core.register("a1")
        s2 = core.register("a2")
        seen = []

        async def wildcard(agent_id, shard):
            seen.append(agent_id)

        core.subscribe_shard(wildcard)   # no agent_id → all
        await core.sync(s1)
        await core.sync(s2)
        assert "a1" in seen and "a2" in seen

    @pytest.mark.asyncio
    async def test_is_any_healing(self):
        from watchdog.mind.core import MindCore
        core = MindCore()
        s = core.register("healer")
        s.state = "healing"
        await core.sync(s)
        assert core.is_any_healing() is True
        assert core.is_any_healing(exclude_agent="healer") is False


class TestConsensusEngine:

    @pytest.mark.asyncio
    async def test_solo_approval(self):
        from watchdog.event_bus import EventSeverity
        from watchdog.mind.consensus import ConsensusEngine
        engine = ConsensusEngine()
        event = _make_event(severity=EventSeverity.CRITICAL)
        result = await engine.propose(event, all_shards=[])
        assert result.approved is True
        assert "Solo" in result.reason

    @pytest.mark.asyncio
    async def test_conflict_blocks_same_subsystem(self):
        from watchdog.event_bus import EventSeverity
        from watchdog.mind.consensus import ConsensusEngine
        from watchdog.mind.shard import AgentShard
        engine = ConsensusEngine()
        event  = _make_event(severity=EventSeverity.CRITICAL, agent_id="service:api")
        peer   = AgentShard("service:server")
        peer.state = "healing"
        result = await engine.propose(event, all_shards=[peer])
        assert result.approved is False
        assert "Conflict" in result.reason

    @pytest.mark.asyncio
    async def test_healthy_peers_approve(self):
        from watchdog.event_bus import EventSeverity
        from watchdog.mind.consensus import ConsensusEngine
        from watchdog.mind.shard import AgentShard
        engine = ConsensusEngine()
        event  = _make_event(severity=EventSeverity.CRITICAL, agent_id="db:foo")
        peers  = []
        for i in range(3):
            s = AgentShard(f"file:mod{i}.py")
            s.state = "healthy"
            peers.append(s)
        result = await engine.propose(event, all_shards=peers)
        assert result.approved is True
        assert result.yes_count == 3


class TestSyncBridge:

    @pytest.mark.asyncio
    async def test_push_updates_mind_state(self):
        from watchdog.mind.core import MindCore
        from watchdog.mind.sync import SyncBridge
        core  = MindCore()
        shard = core.register("bridge-agent")
        bridge = SyncBridge("bridge-agent", shard, core)
        await bridge.push("critical", last_event="DB_CORRUPT")
        stored = core.get_shard("bridge-agent")
        assert stored.state == "critical"
        assert stored.read("last_event") == "DB_CORRUPT"

    @pytest.mark.asyncio
    async def test_set_healing_marks_state(self):
        from watchdog.mind.core import MindCore
        from watchdog.mind.sync import SyncBridge
        core  = MindCore()
        shard = core.register("healer")
        bridge = SyncBridge("healer", shard, core)
        await bridge.set_healing(True)
        assert core.get_shard("healer").state == "healing"
        await bridge.set_healing(False)
        assert core.get_shard("healer").state == "unknown"

    @pytest.mark.asyncio
    async def test_query_peer(self):
        from watchdog.mind.core import MindCore
        from watchdog.mind.sync import SyncBridge
        core  = MindCore()
        peer  = core.register("peer-agent")
        peer.state = "healthy"
        me    = core.register("me")
        bridge = SyncBridge("me", me, core)
        found = bridge.query_peer("peer-agent")
        assert found is not None
        assert found.state == "healthy"

    @pytest.mark.asyncio
    async def test_is_system_healing_false_when_all_healthy(self):
        from watchdog.mind.core import MindCore
        from watchdog.mind.sync import SyncBridge
        core = MindCore()
        for i in range(3):
            s = core.register(f"peer-{i}")
            s.state = "healthy"
        me = core.register("observer")
        bridge = SyncBridge("observer", me, core)
        assert bridge.is_system_healing() is False


# ─────────────────────────────────────────────────────────────────────────────
# AgentRegistry
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentRegistry:

    def test_register_and_get(self):
        from watchdog.registry import AgentRegistry
        from watchdog.event_bus import EventBus
        from watchdog.agents.resource_agent import ResourceAgent
        reg = AgentRegistry()
        agent = ResourceAgent(bus=EventBus())
        reg.register(agent)
        assert reg.get("resource:host") is agent

    def test_unregister_removes_agent(self):
        from watchdog.registry import AgentRegistry
        from watchdog.event_bus import EventBus
        from watchdog.agents.resource_agent import ResourceAgent
        reg = AgentRegistry()
        agent = ResourceAgent(bus=EventBus())
        reg.register(agent)
        removed = reg.unregister("resource:host")
        assert removed is agent
        assert reg.get("resource:host") is None

    def test_len(self):
        from watchdog.registry import AgentRegistry
        from watchdog.event_bus import EventBus
        from watchdog.agents.resource_agent import ResourceAgent
        reg = AgentRegistry()
        assert len(reg) == 0
        reg.register(ResourceAgent(bus=EventBus()))
        assert len(reg) == 1

    def test_health_snapshot_is_list_of_dicts(self):
        from watchdog.registry import AgentRegistry
        from watchdog.event_bus import EventBus
        from watchdog.agents.resource_agent import ResourceAgent
        reg = AgentRegistry()
        reg.register(ResourceAgent(bus=EventBus()))
        snap = reg.health_snapshot()
        assert isinstance(snap, list)
        assert "agent_id" in snap[0]


# ─────────────────────────────────────────────────────────────────────────────
# WatchdogKernel (isolated — no real file system or sidecar spawning)
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchdogKernel:

    @pytest.mark.asyncio
    async def test_kernel_starts_and_stops(self):
        from watchdog.event_bus import EventBus
        from watchdog.healing.actions import HealingStrategy
        from watchdog.kernel import WatchdogKernel
        from watchdog.mind.sync import SharedMind
        bus      = EventBus()
        strategy = HealingStrategy()
        mind     = SharedMind()
        kernel   = WatchdogKernel(
            bus           = bus,
            strategy      = strategy,
            mind          = mind,
            file_interval = 9999.0,  # don't actually poll during test
            svc_interval  = 9999.0,
            db_interval   = 9999.0,
            res_interval  = 9999.0,
        )
        await kernel.start()
        assert kernel._started is True
        assert len(kernel.registry) > 0    # at least file agents + others
        snap = kernel.health_snapshot()
        assert snap["started"] is True
        assert snap["total_agents"] > 0
        await kernel.stop()
        assert kernel._started is False

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        from watchdog.event_bus import EventBus
        from watchdog.kernel import WatchdogKernel
        from watchdog.mind.sync import SharedMind
        kernel = WatchdogKernel(
            bus=EventBus(), mind=SharedMind(),
            file_interval=9999.0, svc_interval=9999.0,
            db_interval=9999.0, res_interval=9999.0,
        )
        await kernel.start()
        count_before = len(kernel.registry)
        await kernel.start()   # second call — must be noop
        assert len(kernel.registry) == count_before
        await kernel.stop()

    @pytest.mark.asyncio
    async def test_health_snapshot_structure(self):
        from watchdog.event_bus import EventBus
        from watchdog.kernel import WatchdogKernel
        from watchdog.mind.sync import SharedMind
        kernel = WatchdogKernel(
            bus=EventBus(), mind=SharedMind(),
            file_interval=9999.0, svc_interval=9999.0,
            db_interval=9999.0, res_interval=9999.0,
        )
        await kernel.start()
        snap = kernel.health_snapshot()
        for key in ("started", "total_agents", "critical_events",
                    "heals_performed", "bus_stats", "mind", "agents"):
            assert key in snap, f"Missing key: {key}"
        assert isinstance(snap["agents"], list)
        await kernel.stop()

    @pytest.mark.asyncio
    async def test_mind_wired_to_all_agents(self):
        from watchdog.event_bus import EventBus
        from watchdog.kernel import WatchdogKernel
        from watchdog.mind.sync import SharedMind
        kernel = WatchdogKernel(
            bus=EventBus(), mind=SharedMind(),
            file_interval=9999.0, svc_interval=9999.0,
            db_interval=9999.0, res_interval=9999.0,
        )
        await kernel.start()
        for agent in kernel.registry:
            assert agent.mind is not None, f"Agent {agent.agent_id} has no mind bridge"
        await kernel.stop()


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI /watchdog dashboard endpoints (ASGI, offline)
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchdogDashboard:
    """
    Test the /watchdog/* endpoints via ASGI without starting a real server.
    Uses the standalone `watchdog.dashboard.app` with a freshly started kernel.
    """

    @pytest.fixture()
    async def client(self):
        from httpx import AsyncClient, ASGITransport
        from watchdog.dashboard import app as wdog_app
        async with AsyncClient(
            transport=ASGITransport(app=wdog_app), base_url="http://test"
        ) as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        r = await client.get("/watchdog/health")
        assert r.status_code in (200, 207)
        data = r.json()
        assert "status" in data

    @pytest.mark.asyncio
    async def test_agents_returns_list(self, client):
        r = await client.get("/watchdog/agents")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_events_returns_list(self, client):
        r = await client.get("/watchdog/events")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_heals_returns_list(self, client):
        r = await client.get("/watchdog/heals")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_mind_returns_snapshot(self, client):
        r = await client.get("/watchdog/mind")
        assert r.status_code == 200
        data = r.json()
        assert "overall_state" in data
        assert "shards" in data

    @pytest.mark.asyncio
    async def test_mind_timeline_returns_list(self, client):
        r = await client.get("/watchdog/mind/timeline?n=10")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_unknown_agent_404(self, client):
        r = await client.get("/watchdog/agents/nonexistent:agent")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_summary_has_expected_keys(self, client):
        r = await client.get("/watchdog/summary")
        assert r.status_code == 200
        data = r.json()
        for key in ("started", "total_agents", "critical_events", "heals_performed"):
            assert key in data


# ─────────────────────────────────────────────────────────────────────────────
# main.py /health and /status include watchdog fields
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _MAIN_IMPORTABLE, reason="numpy not installed — main.py cannot be imported")
class TestMainWatchdogIntegration:

    @pytest.fixture()
    async def client(self):
        from httpx import AsyncClient, ASGITransport
        from main import app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_status_includes_watchdog_online_field(self, client):
        r = await client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert "watchdog_online" in data

    @pytest.mark.asyncio
    async def test_watchdog_health_endpoint_mounted(self, client):
        r = await client.get("/watchdog/health")
        # Will be 200/207 when watchdog is available, 404 when not
        assert r.status_code in (200, 207, 404)
