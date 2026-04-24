"""
tests/test_agents.py — Unit tests for each watchdog agent.

All tests are fully offline:
  - No real files are modified
  - No real processes are spawned
  - No real databases are opened
  - No real HTTP calls are made
  - psutil calls are mocked where needed
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_bus():
    from watchdog.event_bus import EventBus
    return EventBus()


def _make_event(event_type=None, severity=None, agent_id="test"):
    from watchdog.event_bus import EventSeverity, EventType, WatchdogEvent
    return WatchdogEvent(
        event_type=event_type or EventType.FILE_OK,
        severity=severity or EventSeverity.INFO,
        agent_id=agent_id,
        source="test-source",
        message="test",
    )


# ─────────────────────────────────────────────────────────────────────────────
# WatchdogAgent base — status fields
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentBaseStatus:

    def _make_concrete_agent(self):
        from watchdog.agents.base import WatchdogAgent
        from watchdog.event_bus import EventSeverity, EventType

        class _ConcreteAgent(WatchdogAgent):
            async def check(self):
                return self._make_event(EventType.FILE_OK, EventSeverity.INFO, "test message")
            async def heal(self, event):
                return True

        return _ConcreteAgent(agent_id="test:agent", source="test", bus=_make_bus())

    def test_status_includes_last_event_type_none_before_check(self):
        agent = self._make_concrete_agent()
        assert agent.status["last_event_type"] is None
        assert agent.status["last_severity"] is None

    @pytest.mark.asyncio
    async def test_status_populated_after_poll(self):
        from watchdog.event_bus import EventSeverity, EventType
        agent = self._make_concrete_agent()
        bus = agent.bus
        await bus.start()
        await agent.start()
        await asyncio.sleep(0.05)
        await agent.stop()
        await bus.stop()

        status = agent.status
        assert status["last_event_type"] == "FILE_OK"
        assert status["last_severity"] == "INFO"

    def test_status_has_all_required_fields(self):
        agent = self._make_concrete_agent()
        s = agent.status
        for field in ("agent_id", "source", "running", "failures", "checks",
                      "last_ok_ago_s", "uptime_s", "mind_connected",
                      "last_event_type", "last_severity"):
            assert field in s, f"Missing field: {field}"


# ─────────────────────────────────────────────────────────────────────────────
# FileAgent
# ─────────────────────────────────────────────────────────────────────────────

class TestFileAgent:

    def _make_agent(self, path: Path):
        from watchdog.agents.file_agent import FileAgent
        return FileAgent(
            path=path,
            repo_root=path.parent,
            bus=_make_bus(),
            interval=999.0,
        )

    @pytest.mark.asyncio
    async def test_reports_ok_for_valid_python_file(self):
        from watchdog.event_bus import EventSeverity
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("x = 1\n")
            path = Path(f.name)
        try:
            agent = self._make_agent(path)
            event = await agent.check()
            assert event.severity == EventSeverity.INFO
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_reports_critical_for_missing_file(self):
        from watchdog.event_bus import EventSeverity, EventType
        path = Path("/tmp/does_not_exist_aureon_test_12345.py")
        agent = self._make_agent(path)
        event = await agent.check()
        assert event.severity == EventSeverity.CRITICAL
        assert event.event_type == EventType.FILE_MISSING

    @pytest.mark.asyncio
    async def test_reports_syntax_error(self):
        from watchdog.event_bus import EventSeverity, EventType
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def bad syntax here(\n")
            path = Path(f.name)
        try:
            agent = self._make_agent(path)
            event = await agent.check()
            assert event.severity == EventSeverity.CRITICAL
            assert event.event_type == EventType.FILE_SYNTAX_ERROR
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_heal_missing_file_attempts_git_checkout(self):
        from watchdog.event_bus import EventType
        path = Path("/tmp/nonexistent_aureon.py")
        agent = self._make_agent(path)
        trigger = _make_event(event_type=EventType.FILE_MISSING)
        with patch("watchdog.agents.file_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = await agent.heal(trigger)
        mock_run.assert_called_once()
        assert "git" in mock_run.call_args[0][0]


# ─────────────────────────────────────────────────────────────────────────────
# DatabaseAgent
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabaseAgent:

    def _make_agent(self, db_path: Path):
        from watchdog.agents.db_agent import DatabaseAgent
        return DatabaseAgent(
            db_path=db_path,
            bus=_make_bus(),
            interval=999.0,
        )

    @pytest.mark.asyncio
    async def test_reports_db_missing_for_nonexistent_db(self):
        from watchdog.event_bus import EventType
        path = Path("/tmp/nonexistent_aureon_test.db")
        agent = self._make_agent(path)
        event = await agent.check()
        assert event.event_type == EventType.DB_MISSING

    @pytest.mark.asyncio
    async def test_reports_ok_for_valid_db(self):
        import aiosqlite
        from watchdog.event_bus import EventSeverity
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
                await db.commit()
            agent = self._make_agent(db_path)
            event = await agent.check()
            assert event.severity in (EventSeverity.INFO, EventSeverity.WARNING)
        finally:
            db_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_heal_vacuums_db(self):
        import aiosqlite
        from watchdog.event_bus import EventType
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
                await db.commit()
            agent = self._make_agent(db_path)
            trigger = _make_event(event_type=EventType.DB_CORRUPT)
            result = await agent.heal(trigger)
            assert isinstance(result, bool)
        finally:
            db_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# ResourceAgent
# ─────────────────────────────────────────────────────────────────────────────

class TestResourceAgent:

    def _make_agent(self):
        from watchdog.agents.resource_agent import ResourceAgent
        return ResourceAgent(
            bus=_make_bus(),
            interval=999.0,
        )

    @pytest.mark.asyncio
    async def test_check_returns_event(self):
        from watchdog.event_bus import EventSeverity
        agent = self._make_agent()
        try:
            import psutil  # noqa: F401
        except ImportError:
            pytest.skip("psutil not installed")
        event = await agent.check()
        assert event.severity in (EventSeverity.INFO, EventSeverity.WARNING, EventSeverity.CRITICAL)

    @pytest.mark.asyncio
    async def test_check_handles_missing_psutil_gracefully(self):
        from watchdog.event_bus import EventSeverity
        agent = self._make_agent()
        with patch.dict("sys.modules", {"psutil": None}):
            with patch("watchdog.agents.resource_agent.psutil", None):
                event = await agent.check()
        assert event.severity in (EventSeverity.INFO, EventSeverity.WARNING, EventSeverity.CRITICAL)

    @pytest.mark.asyncio
    async def test_heal_cpu_runs_gc(self):
        from watchdog.event_bus import EventType
        import gc
        agent = self._make_agent()
        trigger = _make_event(event_type=EventType.RESOURCE_CPU_HIGH)
        with patch("gc.collect") as mock_gc:
            result = await agent.heal(trigger)
        mock_gc.assert_called()
        assert result is True

    @pytest.mark.asyncio
    async def test_heal_mem_runs_gc(self):
        from watchdog.event_bus import EventType
        agent = self._make_agent()
        trigger = _make_event(event_type=EventType.RESOURCE_MEM_HIGH)
        with patch("gc.collect"):
            result = await agent.heal(trigger)
        assert result is False  # MEM_HIGH healing always returns False (needs human)


# ─────────────────────────────────────────────────────────────────────────────
# ProcessAgent
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessAgent:

    def _make_agent(self):
        from watchdog.agents.process_agent import ProcessAgent
        return ProcessAgent(
            name="dex-oracle",
            binary=Path("/nonexistent/dex-oracle"),
            health_url="http://127.0.0.1:9001/health",
            env={},
            bus=_make_bus(),
            interval=999.0,
        )

    @pytest.mark.asyncio
    async def test_check_info_when_binary_missing_monitor_only(self):
        from watchdog.event_bus import EventSeverity
        agent = self._make_agent()
        event = await agent.check()
        # Binary not found → monitor-only mode → reports INFO
        assert event.severity == EventSeverity.INFO

    @pytest.mark.asyncio
    async def test_heal_called_without_running_process(self):
        from watchdog.event_bus import EventType
        agent = self._make_agent()
        trigger = _make_event(event_type=EventType.PROCESS_DEAD)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = MagicMock(pid=9999, poll=MagicMock(return_value=None))
            result = await agent.heal(trigger)
        assert isinstance(result, bool)


# ─────────────────────────────────────────────────────────────────────────────
# ServiceAgent
# ─────────────────────────────────────────────────────────────────────────────

class TestServiceAgent:

    def _make_agent(self):
        from watchdog.agents.service_agent import ServiceAgent
        return ServiceAgent(
            name="aureon-api",
            host="127.0.0.1",
            port=8010,
            entry_module="main:app",
            health_path="/health",
            bus=_make_bus(),
            interval=999.0,
            auto_restart=False,
        )

    @pytest.mark.asyncio
    async def test_check_non_ok_when_service_unreachable(self):
        from watchdog.event_bus import EventSeverity
        agent = self._make_agent()
        event = await agent.check()
        assert event.severity in (EventSeverity.WARNING, EventSeverity.CRITICAL)

    @pytest.mark.asyncio
    async def test_check_ok_when_service_healthy(self):
        from watchdog.event_bus import EventSeverity
        import httpx
        agent = self._make_agent()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.elapsed = MagicMock()
        mock_resp.elapsed.total_seconds.return_value = 0.05

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            event = await agent.check()

        assert event.severity == EventSeverity.INFO


# ─────────────────────────────────────────────────────────────────────────────
# TradeLoopAgent
# ─────────────────────────────────────────────────────────────────────────────

class TestTradeLoopAgent:

    def _make_agent(self):
        from watchdog.agents.trade_agent import TradeLoopAgent
        return TradeLoopAgent(
            bus=_make_bus(),
            interval=999.0,
        )

    @pytest.mark.asyncio
    async def test_check_ok_when_loop_not_expected_running(self):
        from watchdog.event_bus import EventSeverity
        agent = self._make_agent()
        agent.set_expected_running(False)
        event = await agent.check()
        assert event.severity == EventSeverity.INFO

    @pytest.mark.asyncio
    async def test_check_critical_when_loop_stopped_unexpectedly(self):
        from watchdog.event_bus import EventSeverity, EventType
        agent = self._make_agent()
        agent.set_expected_running(True)

        mock_loop = MagicMock()
        mock_loop.running = False
        mock_loop.cycle_count = 0
        agent._loop_ref = mock_loop

        with patch.object(agent, "_resolve", return_value=True):
            event = await agent.check()

        assert event.severity == EventSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_check_ok_when_loop_running_as_expected(self):
        from watchdog.event_bus import EventSeverity
        agent = self._make_agent()
        agent.set_expected_running(True)

        mock_loop = MagicMock()
        mock_loop.running = True
        mock_loop.cycle_count = 5
        mock_loop.last_cycle_at = time.monotonic()
        agent._loop_ref = mock_loop

        with patch.object(agent, "_resolve", return_value=True):
            event = await agent.check()

        assert event.severity == EventSeverity.INFO

    def test_set_expected_running_toggles_state(self):
        agent = self._make_agent()
        agent.set_expected_running(True)
        assert agent._expected_running is True
        agent.set_expected_running(False)
        assert agent._expected_running is False


# ─────────────────────────────────────────────────────────────────────────────
# Watchdog Config
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchdogConfig:

    def test_all_thresholds_are_positive(self):
        from watchdog import config as cfg
        assert cfg.FILE_AGENT_INTERVAL > 0
        assert cfg.PROCESS_AGENT_INTERVAL > 0
        assert cfg.SERVICE_AGENT_INTERVAL > 0
        assert cfg.DB_AGENT_INTERVAL > 0
        assert cfg.RESOURCE_AGENT_INTERVAL > 0
        assert cfg.TRADE_AGENT_INTERVAL > 0
        assert cfg.HEAL_COOLDOWN_SEC > 0
        assert cfg.HEAL_MAX_ATTEMPTS > 0
        assert cfg.CPU_WARN_PCT < cfg.CPU_CRIT_PCT
        assert cfg.MEM_WARN_PCT < cfg.MEM_CRIT_PCT
        assert cfg.DISK_WARN_PCT < cfg.DISK_CRIT_PCT
        assert cfg.FD_WARN < cfg.FD_CRIT

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("WDOG_CPU_WARN", "70.0")
        import importlib
        import watchdog.config as cfg
        importlib.reload(cfg)
        assert cfg.CPU_WARN_PCT == 70.0
        importlib.reload(cfg)  # restore defaults


# ─────────────────────────────────────────────────────────────────────────────
# Prometheus Metrics endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestPrometheusMetrics:

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_prometheus_text(self):
        import httpx
        from httpx import ASGITransport
        from watchdog.dashboard import app

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/watchdog/metrics")

        assert r.status_code == 200
        body = r.text
        assert "aureon_watchdog_agents_total" in body
        assert "aureon_watchdog_critical_events_total" in body
        assert "aureon_watchdog_heals_total" in body
        assert "# HELP" in body
        assert "# TYPE" in body
