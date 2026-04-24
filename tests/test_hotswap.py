"""
tests/test_hotswap.py — Unit tests for the hot-swap controller.

All tests are offline (no real compilation triggered).
"""
from __future__ import annotations
import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestHotSwapLoader:

    def test_safe_import_returns_module_from_sys_modules(self):
        from hotswap.loader import safe_import
        fake = types.ModuleType("_fake_hs_test")
        sys.modules["_fake_hs_test"] = fake
        try:
            assert safe_import("_fake_hs_test") is fake
        finally:
            sys.modules.pop("_fake_hs_test", None)

    def test_safe_import_returns_none_for_missing(self):
        from hotswap.loader import safe_import
        result = safe_import("_nonexistent_module_aureon_xyz")
        assert result is None

    def test_extension_available_false_for_py_module(self):
        from hotswap.loader import extension_available
        # Pure Python modules are not .so/.pyd
        assert extension_available("os") is False

    def test_reload_extension_returns_bool(self):
        from hotswap.loader import reload_extension
        # os is always importable; reload should succeed
        result = reload_extension("os")
        assert isinstance(result, bool)
        assert result is True

    def test_load_with_fallback_uses_fallback_when_no_so(self):
        from hotswap.loader import load_with_fallback
        # os is pure Python (no .so) → fallback to sys
        mod = load_with_fallback("os", "sys")
        assert mod is sys


class TestHotSwapController:

    def _make_controller(self):
        from hotswap.controller import HotSwapController
        # Pass a very long poll interval so watcher doesn't actually run
        ctl = HotSwapController(poll_interval_s=9999.0)
        # Clear auto-discovered targets so tests are hermetic
        ctl._targets.clear()
        ctl._cython_rebuilders.clear()
        ctl._rust_rebuilders.clear()
        return ctl

    def test_stats_initial(self):
        ctl = self._make_controller()
        s = ctl.stats
        assert s["rebuilds"] == 0
        assert s["failures"] == 0
        assert s["targets"] == 0

    def test_add_cython_target_increments_targets(self, tmp_path):
        ctl = self._make_controller()
        pyx = tmp_path / "fake.pyx"
        pyx.write_text("# fake")
        ctl.add_cython_target(pyx, "fake.module")
        assert ctl.stats["targets"] == 1

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        from hotswap.controller import HotSwapController
        ctl = HotSwapController(poll_interval_s=9999.0)
        ctl._targets.clear()
        ctl.start()
        try:
            assert ctl._task is not None
            assert not ctl._task.done()
        finally:
            ctl.stop()
            if ctl._task:
                try:
                    await ctl._task
                except asyncio.CancelledError:
                    pass

    def test_stop_sets_running_false(self):
        from hotswap.controller import HotSwapController
        ctl = HotSwapController(poll_interval_s=9999.0)
        ctl._targets.clear()
        ctl._running = True
        ctl.stop()
        assert ctl._running is False

    def test_watch_target_changed_detects_mtime(self, tmp_path):
        from hotswap.controller import _WatchTarget
        f = tmp_path / "test.pyx"
        f.write_text("x = 1")
        t = _WatchTarget(path=f, kind="cython", module="test")
        t.snapshot()
        assert not t.changed()  # no change yet
        import time; time.sleep(0.01)
        f.write_text("x = 2")   # modify
        assert t.changed()      # should detect
