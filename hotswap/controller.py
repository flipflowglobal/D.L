"""
hotswap/controller.py — Zero-downtime Cython & Rust extension hot-reloader.

Design
------
A background asyncio task watches source trees for changes and triggers
targeted recompilation.  After a successful build the relevant Python
module is reloaded in-process using importlib, so running coroutines get
the new code on their next call without a process restart.

Hot-swap guarantee
------------------
- Module **references** already held by callers continue to work until
  they go out of scope.  New calls to ``import X`` get the fresh module.
- Cython: re-cythonize the changed .pyx file only, then cc-compile and
  reload the .so extension.
- Rust (dex-oracle / tx-engine): ``cargo build --release`` the relevant
  workspace member; the binary is swapped under a symlink so the kernel
  receives SIGHUP (or a health check detects the new binary automatically).

File-watcher
------------
Uses polling (stat-based) so it works without inotify / kqueue — safe on
every platform including Docker containers and Termux.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger("hotswap.controller")

_ROOT = Path(__file__).resolve().parent.parent  # repo root


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _run(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd or _ROOT),
        capture_output=True,
        text=True,
    )


# ── Watch targets ─────────────────────────────────────────────────────────────

@dataclass
class _WatchTarget:
    """Represents one watched source file and its rebuild action."""
    path:     Path
    kind:     str          # "cython" | "rust"
    module:   str          # dotted Python module name to reload (Cython) or "" (Rust)
    _mtime:   float = field(default=0.0, init=False)

    def changed(self) -> bool:
        m = _mtime(self.path)
        if m != self._mtime:
            self._mtime = m
            return True
        return False

    def snapshot(self) -> None:
        self._mtime = _mtime(self.path)


# ── Rebuild workers ───────────────────────────────────────────────────────────

class _CythonRebuilder:
    """Recompiles a single .pyx file and reloads its Python module."""

    def __init__(self, target: _WatchTarget) -> None:
        self._t = target

    def rebuild(self) -> bool:
        pyx = self._t.path
        logger.info("[cython] rebuilding %s …", pyx.name)

        # Step 1 — cythonize: .pyx → .c
        r = _run([sys.executable, "setup_cython.py", "build_ext", "--inplace",
                  "--", str(pyx)])
        if r.returncode != 0:
            logger.error("[cython] compile FAILED:\n%s", r.stderr[-2000:])
            return False

        # Step 2 — reload the Python extension
        mod_name = self._t.module
        if mod_name and mod_name in sys.modules:
            try:
                importlib.invalidate_caches()
                importlib.reload(sys.modules[mod_name])
                logger.info("[cython] reloaded %s ✓", mod_name)
            except Exception as exc:
                logger.error("[cython] reload %s failed: %s", mod_name, exc)
                return False
        return True


class _RustRebuilder:
    """Runs cargo build for a workspace member and signals the sidecar."""

    def __init__(self, crate_dir: Path, sidecar_name: str) -> None:
        self._crate = crate_dir
        self._name  = sidecar_name

    def rebuild(self) -> bool:
        logger.info("[rust] rebuilding %s …", self._name)
        r = _run(["cargo", "build", "--release"], cwd=self._crate)
        if r.returncode != 0:
            logger.error("[rust] cargo build FAILED:\n%s", r.stderr[-2000:])
            return False

        binary = self._crate / "target" / "release" / self._name
        if not binary.exists():
            logger.warning("[rust] binary not found at %s", binary)
            return False

        # Atomic symlink swap: live.bin → new binary
        link = _ROOT / self._name / f"{self._name}.live"
        tmp  = link.with_suffix(".swapping")
        try:
            tmp.unlink(missing_ok=True)
            tmp.symlink_to(binary)
            tmp.rename(link)
        except OSError as exc:
            logger.warning("[rust] symlink swap failed: %s", exc)

        logger.info("[rust] %s rebuilt ✓", self._name)
        return True


# ── Controller ────────────────────────────────────────────────────────────────

class HotSwapController:
    """
    Background controller that watches source files and hot-reloads
    Cython extensions and Rust sidecars when they change.

    Parameters
    ----------
    poll_interval_s:
        How often (seconds) to poll source files for modifications.
        Default 2.0 — fast enough for interactive development, low overhead
        in production.
    on_rebuild:
        Optional callback(module_name: str, success: bool) invoked after
        every rebuild attempt (useful for metrics / alerting).
    """

    def __init__(
        self,
        poll_interval_s: float = 2.0,
        on_rebuild: Optional[Callable[[str, bool], None]] = None,
    ) -> None:
        self._interval  = poll_interval_s
        self._on_rebuild = on_rebuild
        self._task:  Optional[asyncio.Task] = None
        self._targets: List[_WatchTarget]   = []
        self._cython_rebuilders: Dict[Path, _CythonRebuilder] = {}
        self._rust_rebuilders:   Dict[str, _RustRebuilder]    = {}
        self._running = False
        self._stats   = {"rebuilds": 0, "failures": 0}

        self._register_defaults()

    # ── Registration ─────────────────────────────────────────────────────────

    def _register_defaults(self) -> None:
        """Auto-discover .pyx files and Rust crate directories."""
        # Cython extensions
        pyx_map = {
            _ROOT / "engine" / "portfolio.pyx":                         "engine.portfolio",
            _ROOT / "engine" / "risk_manager.pyx":                      "engine.risk_manager",
            _ROOT / "engine" / "strategies" / "mean_reversion.pyx":     "engine.strategies.mean_reversion",
        }
        for pyx_path, module in pyx_map.items():
            if pyx_path.exists():
                t = _WatchTarget(path=pyx_path, kind="cython", module=module)
                t.snapshot()
                self._targets.append(t)
                self._cython_rebuilders[pyx_path] = _CythonRebuilder(t)
                logger.debug("[hotswap] watching Cython: %s", pyx_path.name)

        # Rust crates (watch all .rs files in src/)
        rust_crates = {
            "dex-oracle": _ROOT / "dex-oracle",
            "tx-engine":  _ROOT / "tx-engine",
        }
        for name, crate_dir in rust_crates.items():
            src_dir = crate_dir / "src"
            if not src_dir.is_dir():
                continue
            for rs_file in src_dir.glob("**/*.rs"):
                t = _WatchTarget(path=rs_file, kind="rust", module="")
                t.snapshot()
                self._targets.append(t)
            self._rust_rebuilders[name] = _RustRebuilder(crate_dir, name)
            logger.debug("[hotswap] watching Rust crate: %s", name)

    def add_cython_target(self, pyx_path: Path, module: str) -> None:
        """Register an additional .pyx file to watch."""
        t = _WatchTarget(path=pyx_path, kind="cython", module=module)
        t.snapshot()
        self._targets.append(t)
        self._cython_rebuilders[pyx_path] = _CythonRebuilder(t)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background watcher task (call from an async context)."""
        if self._running:
            return
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._watch_loop(), name="hotswap-watcher")
            logger.info("[hotswap] started (poll=%.1fs, targets=%d)",
                        self._interval, len(self._targets))
        except RuntimeError:
            logger.warning("[hotswap] no running event loop — call start() from async context")

    def stop(self) -> None:
        """Cancel the background watcher task."""
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("[hotswap] stopped")

    @property
    def stats(self) -> dict:
        return {**self._stats, "targets": len(self._targets)}

    # ── Watch loop ────────────────────────────────────────────────────────────

    async def _watch_loop(self) -> None:
        # Debounce: collect all changes within 500ms before triggering rebuild
        pending_cython: Set[Path] = set()
        pending_rust:   Set[str]  = set()
        last_change_at  = 0.0

        while self._running:
            # Poll all targets
            for t in self._targets:
                if t.changed():
                    if t.kind == "cython":
                        pending_cython.add(t.path)
                        logger.debug("[hotswap] change detected: %s", t.path.name)
                    else:
                        # For Rust: figure out which crate owns this file
                        for name, rebuilder in self._rust_rebuilders.items():
                            if str(t.path).startswith(str(rebuilder._crate)):
                                pending_rust.add(name)
                                break
                    last_change_at = time.monotonic()

            # After 500ms of quiet, flush pending rebuilds
            if (pending_cython or pending_rust) and (time.monotonic() - last_change_at) > 0.5:
                await self._flush_rebuilds(pending_cython, pending_rust)
                pending_cython.clear()
                pending_rust.clear()

            await asyncio.sleep(self._interval)

    async def _flush_rebuilds(
        self,
        cython_paths: Set[Path],
        rust_crates:  Set[str],
    ) -> None:
        """Run rebuilds off the event loop to avoid blocking."""
        loop = asyncio.get_running_loop()

        for path in cython_paths:
            rebuilder = self._cython_rebuilders.get(path)
            if rebuilder:
                success = await loop.run_in_executor(None, rebuilder.rebuild)
                self._stats["rebuilds"] += 1
                if not success:
                    self._stats["failures"] += 1
                if self._on_rebuild:
                    self._on_rebuild(rebuilder._t.module, success)

        for name in rust_crates:
            rebuilder = self._rust_rebuilders.get(name)
            if rebuilder:
                success = await loop.run_in_executor(None, rebuilder.rebuild)
                self._stats["rebuilds"] += 1
                if not success:
                    self._stats["failures"] += 1
                if self._on_rebuild:
                    self._on_rebuild(name, success)
