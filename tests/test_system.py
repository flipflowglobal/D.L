"""
tests/test_system.py — Tests for the autonomous system kernel layer.

Covers:
  - kernel/watchdog_kernel.py
  - agents/watchdog_agent.py
  - core/state_manager.py
  - core/integrity.py
"""

import json
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# core/integrity.py
# ─────────────────────────────────────────────────────────────────────────────


class TestIntegrityChecker:
    """Tests for core.integrity.IntegrityChecker."""

    def test_generate_manifest(self, tmp_path):
        """generate_manifest() creates a JSON manifest file."""
        from core.integrity import IntegrityChecker

        manifest_path = tmp_path / "manifest.json"
        repo_root = Path(__file__).resolve().parent.parent

        checker = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        manifest = checker.generate_manifest()

        assert manifest_path.exists()
        assert "files" in manifest
        assert "generated_at" in manifest
        assert len(manifest["files"]) > 0

    def test_verify_all_first_run(self, tmp_path):
        """verify_all() on first run generates manifest and passes."""
        from core.integrity import IntegrityChecker

        manifest_path = tmp_path / "manifest.json"
        repo_root = Path(__file__).resolve().parent.parent

        checker = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        result = checker.verify_all()

        assert result["passed"] is True
        assert result["new_files"] is True
        assert result["checked"] > 0

    def test_verify_all_subsequent_run(self, tmp_path):
        """verify_all() on second run validates against existing manifest."""
        from core.integrity import IntegrityChecker

        manifest_path = tmp_path / "manifest.json"
        repo_root = Path(__file__).resolve().parent.parent

        checker = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        # First run — generate
        checker.generate_manifest()

        # Second run — verify
        checker2 = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        result = checker2.verify_all()

        assert result["passed"] is True
        assert result["new_files"] is False
        assert len(result["failures"]) == 0

    def test_verify_detects_tampering(self, tmp_path):
        """verify_all() detects changed file hashes."""
        from core.integrity import IntegrityChecker

        manifest_path = tmp_path / "manifest.json"
        repo_root = Path(__file__).resolve().parent.parent

        checker = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        manifest = checker.generate_manifest()

        # Tamper with a hash in the manifest
        files = manifest["files"]
        if files:
            first_key = next(iter(files))
            files[first_key]["sha256"] = "0" * 64
            with open(manifest_path, "w") as f:
                json.dump(manifest, f)

        checker2 = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        result = checker2.verify_all()

        assert result["passed"] is False
        assert len(result["failures"]) >= 1

    def test_verify_file_single(self, tmp_path):
        """verify_file() checks a single file."""
        from core.integrity import IntegrityChecker

        manifest_path = tmp_path / "manifest.json"
        repo_root = Path(__file__).resolve().parent.parent

        checker = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        checker.generate_manifest()

        assert checker.verify_file("main.py") is True
        # Unknown file — not in manifest, should pass (skip)
        assert checker.verify_file("nonexistent_file.xyz") is True

    def test_get_manifest(self, tmp_path):
        """get_manifest() loads from disk if not in memory."""
        from core.integrity import IntegrityChecker

        manifest_path = tmp_path / "manifest.json"
        repo_root = Path(__file__).resolve().parent.parent

        checker = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        checker.generate_manifest()

        # New instance should load from disk
        checker2 = IntegrityChecker(
            manifest_path=manifest_path, repo_root=repo_root
        )
        manifest = checker2.get_manifest()
        assert manifest is not None
        assert "files" in manifest


# ─────────────────────────────────────────────────────────────────────────────
# core/state_manager.py
# ─────────────────────────────────────────────────────────────────────────────


class TestStateManager:
    """Tests for core.state_manager.StateManager."""

    @pytest.fixture
    def tmp_db(self, tmp_path):
        return tmp_path / "test_state.db"

    @pytest.mark.asyncio
    async def test_init_db(self, tmp_db):
        """init_db() creates the snapshots table."""
        from core.state_manager import StateManager

        mgr = StateManager(db_path=tmp_db)
        await mgr.init_db()
        assert tmp_db.exists()

    @pytest.mark.asyncio
    async def test_snapshot_creates_record(self, tmp_db):
        """snapshot() inserts a record into the database."""
        from core.state_manager import StateManager

        mgr = StateManager(db_path=tmp_db)
        result = await mgr.snapshot()

        assert "timestamp" in result
        assert "checksum" in result
        assert result["agent_count"] >= 0

    @pytest.mark.asyncio
    async def test_recover_empty_db(self, tmp_db):
        """recover() returns None when no snapshots exist."""
        from core.state_manager import StateManager

        mgr = StateManager(db_path=tmp_db)
        await mgr.init_db()

        state = await mgr.recover()
        assert state is None

    @pytest.mark.asyncio
    async def test_snapshot_and_recover(self, tmp_db):
        """snapshot() then recover() returns matching state."""
        from core.state_manager import StateManager

        mgr = StateManager(db_path=tmp_db)
        snap = await mgr.snapshot()

        state = await mgr.recover()
        assert state is not None
        assert state["timestamp"] == snap["timestamp"]
        assert "version" in state

    @pytest.mark.asyncio
    async def test_get_history(self, tmp_db):
        """get_history() returns recent snapshots."""
        from core.state_manager import StateManager

        mgr = StateManager(db_path=tmp_db)

        # Create 3 snapshots
        for _ in range(3):
            await mgr.snapshot()

        history = await mgr.get_history(limit=10)
        assert len(history) == 3
        # Most recent first
        assert history[0]["id"] > history[1]["id"]

    @pytest.mark.asyncio
    async def test_checksum_validation(self, tmp_db):
        """recover() validates checksum integrity."""
        import aiosqlite
        from core.state_manager import StateManager

        mgr = StateManager(db_path=tmp_db)
        await mgr.snapshot()

        # Corrupt the checksum
        async with aiosqlite.connect(str(tmp_db)) as db:
            await db.execute(
                "UPDATE snapshots SET checksum = 'corrupted' WHERE id = 1"
            )
            await db.commit()

        state = await mgr.recover()
        assert state is None  # Should reject corrupted data

    @pytest.mark.asyncio
    async def test_cleanup_old_snapshots(self, tmp_db):
        """_cleanup() removes snapshots beyond MAX_SNAPSHOTS."""
        from core.state_manager import StateManager

        mgr = StateManager(db_path=tmp_db)
        # Temporarily reduce max snapshots for testing
        import core.state_manager as sm_mod

        old_max = sm_mod.MAX_SNAPSHOTS
        sm_mod.MAX_SNAPSHOTS = 3
        try:
            for _ in range(5):
                await mgr.snapshot()

            history = await mgr.get_history(limit=10)
            assert len(history) == 3
        finally:
            sm_mod.MAX_SNAPSHOTS = old_max


# ─────────────────────────────────────────────────────────────────────────────
# agents/watchdog_agent.py
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentWatchdog:
    """Tests for agents.watchdog_agent.AgentWatchdog."""

    @pytest.mark.asyncio
    async def test_check_all_empty_registry(self):
        """check_all() returns a valid report with expected fields."""
        from agents.watchdog_agent import AgentWatchdog

        watchdog = AgentWatchdog()
        report = await watchdog.check_all()

        assert "checked" in report
        assert "healthy" in report
        assert "restarted" in report
        assert "errors" in report
        assert "timestamp" in report
        assert isinstance(report["checked"], int)
        assert isinstance(report["healthy"], list)
        assert isinstance(report["restarted"], list)
        assert isinstance(report["errors"], list)

    @pytest.mark.asyncio
    async def test_check_all_with_idle_agents(self):
        """check_all() correctly identifies idle agents as healthy."""
        from agents.watchdog_agent import AgentWatchdog
        from intelligence.trading_agent import (
            TradingAgentConfig,
            Strategy,
            registry,
        )

        # Create a test agent
        config = TradingAgentConfig(name="Test-WD", strategy=Strategy.ARB)
        agent = registry.create(config)

        try:
            watchdog = AgentWatchdog()
            report = await watchdog.check_all()

            assert report["checked"] >= 1
            assert agent.id in report["healthy"]
        finally:
            registry.remove(agent.id)

    def test_report_before_check(self):
        """report() returns None before any check has run."""
        from agents.watchdog_agent import AgentWatchdog

        watchdog = AgentWatchdog()
        assert watchdog.report() is None


# ─────────────────────────────────────────────────────────────────────────────
# kernel/watchdog_kernel.py
# ─────────────────────────────────────────────────────────────────────────────


class TestWatchdogKernel:
    """Tests for kernel.watchdog_kernel.WatchdogKernel."""

    def test_kernel_instantiation(self):
        """WatchdogKernel can be instantiated."""
        from kernel.watchdog_kernel import WatchdogKernel

        kernel = WatchdogKernel()
        assert kernel._running is False
        assert kernel._api_proc is None
        assert kernel._restarts == 0

    def test_kernel_status_before_start(self):
        """status() returns valid dict before kernel is started."""
        from kernel.watchdog_kernel import WatchdogKernel

        kernel = WatchdogKernel()
        status = kernel.status()

        assert status["running"] is False
        assert status["api_server"]["running"] is False
        assert status["api_server"]["pid"] is None
        assert status["api_server"]["restarts"] == 0
        assert "config" in status

    @pytest.mark.asyncio
    async def test_kernel_check_health_no_server(self):
        """_check_health() returns False when no server is running."""
        import httpx
        from kernel.watchdog_kernel import WatchdogKernel

        kernel = WatchdogKernel()
        kernel._client = httpx.AsyncClient(timeout=1.0)

        try:
            healthy = await kernel._check_health()
            assert healthy is False
        finally:
            await kernel._client.aclose()

    @pytest.mark.asyncio
    async def test_kernel_shutdown_idempotent(self):
        """_shutdown() is safe to call even when nothing is running."""
        from kernel.watchdog_kernel import WatchdogKernel

        kernel = WatchdogKernel()
        kernel._start_time = 0
        # Should not raise
        await kernel._shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# Integration: imports
# ─────────────────────────────────────────────────────────────────────────────


class TestModuleImports:
    """Verify all new modules import cleanly."""

    def test_import_kernel(self):
        from kernel import watchdog_kernel  # noqa: F401
        assert hasattr(watchdog_kernel, 'WatchdogKernel')

    def test_import_agents(self):
        from agents import watchdog_agent  # noqa: F401
        assert hasattr(watchdog_agent, 'AgentWatchdog')

    def test_import_core(self):
        from core import state_manager, integrity  # noqa: F401
        assert hasattr(state_manager, 'StateManager')
        assert hasattr(integrity, 'IntegrityChecker')

    def test_watchdog_kernel_classes(self):
        from kernel.watchdog_kernel import WatchdogKernel

        assert WatchdogKernel is not None

    def test_watchdog_agent_classes(self):
        from agents.watchdog_agent import AgentWatchdog

        assert AgentWatchdog is not None

    def test_state_manager_singleton(self):
        from core.state_manager import state_manager

        assert state_manager is not None

    def test_integrity_checker_class(self):
        from core.integrity import IntegrityChecker

        assert IntegrityChecker is not None
