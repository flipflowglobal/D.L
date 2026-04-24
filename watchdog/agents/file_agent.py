"""
watchdog/agents/file_agent.py — Per-Python-file integrity watchdog.

One FileAgent is spawned for EVERY Python file in the repository.
Each agent independently monitors its assigned file for:

  1. Existence        — file was deleted
  2. Hash integrity   — unexpected modification (SHA-256)
  3. Syntax validity  — ast.parse() detects syntax errors instantly
  4. Import health    — importlib can actually load the module (periodic)

Self-healing actions:
  - Syntax error  → attempt git checkout to restore last-known-good version
  - Missing file  → attempt git checkout
  - Import error  → log + attempt importlib.invalidate_caches() + reload
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from watchdog.agents.base import WatchdogAgent
from watchdog.event_bus import EventBus, EventSeverity, EventType, WatchdogEvent

logger = logging.getLogger("watchdog.agent.file")

# How often to attempt a full import check (more expensive than hash)
_IMPORT_CHECK_EVERY = 6   # every N poll cycles


def _sha256(path: Path) -> Optional[str]:
    """Return hex SHA-256 of *path*, or None on read failure."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _syntax_ok(path: Path) -> tuple[bool, str]:
    """Return (True, "") if syntax is valid, else (False, error_message)."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        ast.parse(source, filename=str(path))
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, str(e)


def _import_ok(path: Path, repo_root: Path) -> tuple[bool, str]:
    """
    Try to import the module corresponding to *path* via importlib.
    Returns (True, "") on success or (False, error) on failure.
    """
    try:
        # Derive dotted module name from path relative to repo_root
        rel = path.relative_to(repo_root)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            return True, ""   # top-level __init__ edge case
        module_name = ".".join(parts)

        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None:
            return False, "importlib.util.spec_from_file_location returned None"

        mod = importlib.util.module_from_spec(spec)
        # Execute in a throwaway namespace — don't pollute sys.modules
        spec.loader.exec_module(mod)    # type: ignore[union-attr]
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


class FileAgent(WatchdogAgent):
    """
    Watchdog agent for a single Python source file.

    Spawns one instance per .py file across the entire repository.
    """

    def __init__(
        self,
        path:      Path,
        repo_root: Path,
        bus:       EventBus,
        interval:  float = 15.0,
    ) -> None:
        agent_id = "file:" + str(path.relative_to(repo_root))
        super().__init__(
            agent_id = agent_id,
            source   = str(path),
            bus      = bus,
            interval = interval,
        )
        self.path      = path
        self.repo_root = repo_root

        # Baseline state — set on first successful check
        self._baseline_hash: Optional[str] = None
        self._baseline_time: float         = 0.0
        self._hash_established             = False

    # ── Check ─────────────────────────────────────────────────────────────────

    async def check(self) -> WatchdogEvent:
        # ── 1. Existence ──────────────────────────────────────────────────────
        if not self.path.exists():
            return self._make_event(
                EventType.FILE_MISSING,
                EventSeverity.CRITICAL,
                f"File has been deleted: {self.path}",
            )

        current_hash = _sha256(self.path)
        if current_hash is None:
            return self._make_event(
                EventType.FILE_MISSING,
                EventSeverity.WARNING,
                "File unreadable (permissions?)",
            )

        # ── 2. Establish baseline on first check ──────────────────────────────
        if not self._hash_established:
            self._baseline_hash = current_hash
            self._baseline_time = time.time()
            self._hash_established = True
            self.log.debug("Baseline hash recorded: %s", current_hash[:12])

        # ── 3. Syntax check ───────────────────────────────────────────────────
        ok, err = _syntax_ok(self.path)
        if not ok:
            return self._make_event(
                EventType.FILE_SYNTAX_ERROR,
                EventSeverity.CRITICAL,
                err,
                details={"hash": current_hash},
            )

        # ── 4. Hash change detection ──────────────────────────────────────────
        if current_hash != self._baseline_hash:
            old = self._baseline_hash[:12] if self._baseline_hash else "?"
            self._baseline_hash = current_hash   # accept new state
            self._baseline_time = time.time()
            return self._make_event(
                EventType.FILE_MODIFIED,
                EventSeverity.INFO,
                f"File modified (hash {old}… → {current_hash[:12]}…)",
                details={"new_hash": current_hash},
            )

        # ── 5. Periodic import check ──────────────────────────────────────────
        if self._check_count % _IMPORT_CHECK_EVERY == 0:
            imp_ok, imp_err = _import_ok(self.path, self.repo_root)
            if not imp_ok:
                return self._make_event(
                    EventType.FILE_IMPORT_ERROR,
                    EventSeverity.WARNING,
                    f"Import failed: {imp_err}",
                    details={"hash": current_hash, "import_error": imp_err},
                )

        return self._make_event(
            EventType.FILE_OK,
            EventSeverity.INFO,
            "OK",
            details={"hash": current_hash[:12]},
        )

    # ── Heal ──────────────────────────────────────────────────────────────────

    async def heal(self, event: WatchdogEvent) -> bool:
        if event.event_type in (EventType.FILE_MISSING, EventType.FILE_SYNTAX_ERROR):
            return self._git_restore()
        if event.event_type == EventType.FILE_IMPORT_ERROR:
            return self._invalidate_and_retry()
        return False

    def _git_restore(self) -> bool:
        """Attempt `git checkout HEAD -- <file>` to restore the last committed version."""
        try:
            rel = str(self.path.relative_to(self.repo_root))
            result = subprocess.run(
                ["git", "checkout", "HEAD", "--", rel],
                cwd=str(self.repo_root),
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                self._hash_established = False   # re-baseline on next check
                self.log.warning("Restored %s via git checkout", rel)
                return True
            self.log.error("git restore failed: %s", result.stderr.strip())
            return False
        except Exception as exc:
            self.log.error("git restore exception: %s", exc)
            return False

    def _invalidate_and_retry(self) -> bool:
        """Invalidate import caches and retry loading the module."""
        try:
            importlib.invalidate_caches()
            ok, err = _import_ok(self.path, self.repo_root)
            if ok:
                self.log.info("Import recovered after cache invalidation: %s", self.path.name)
                return True
            self.log.warning("Import still failing after cache invalidation: %s", err)
            return False
        except Exception as exc:
            self.log.error("Import invalidation raised: %s", exc)
            return False
