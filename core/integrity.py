"""
core/integrity.py — Binary Integrity Enforcement
==================================================

Verifies the integrity of critical system files on startup:
  - Python source modules (hash verification)
  - Rust sidecar binaries (existence + hash when present)
  - Configuration files (existence checks)

The checker computes SHA-256 hashes of critical files and compares
them against a stored manifest. On first run, it generates the
manifest. On subsequent runs, it detects tampering.

Architecture:
  IntegrityChecker
    ├── generate_manifest()  → compute and store hashes
    ├── verify_all()         → check all files against manifest
    ├── verify_file()        → check a single file
    └── get_manifest()       → return the current manifest

Manifest format (JSON):
  {
    "generated_at": "...",
    "files": {
      "path/to/file.py": {"sha256": "...", "size": 1234}
    }
  }

Usage:
  from core.integrity import IntegrityChecker
  checker = IntegrityChecker()
  result = checker.verify_all()
  assert result["passed"]
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aureon.integrity")

# ── Configuration ─────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = Path(
    os.getenv(
        "INTEGRITY_MANIFEST_PATH",
        str(REPO_ROOT / ".integrity_manifest.json"),
    )
)

# Critical files to verify (relative to repo root)
CRITICAL_FILES: List[str] = [
    "main.py",
    "config.py",
    "supervisor.py",
    "database.py",
    "intelligence/trading_agent.py",
    "intelligence/autonomy.py",
    "intelligence/memory.py",
    "engine/market_data.py",
    "engine/portfolio.py",
    "engine/risk_manager.py",
    "engine/execution/executor.py",
    "kernel/watchdog_kernel.py",
    "agents/watchdog_agent.py",
    "core/state_manager.py",
    "core/integrity.py",
]

# Optional binaries — checked for existence, hashed if present
OPTIONAL_BINARIES: List[str] = [
    "dex-oracle/target/release/dex-oracle",
    "tx-engine/target/release/tx-engine",
]


def _sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class IntegrityChecker:
    """
    Verifies the integrity of critical system files.

    On first run (no manifest), generates a manifest of SHA-256 hashes.
    On subsequent runs, compares current hashes against the manifest
    and reports any mismatches (potential tampering).
    """

    def __init__(
        self,
        manifest_path: Optional[Path] = None,
        repo_root: Optional[Path] = None,
    ) -> None:
        self._manifest_path = manifest_path or MANIFEST_PATH
        self._repo_root = repo_root or REPO_ROOT
        self._manifest: Optional[Dict[str, Any]] = None

    def generate_manifest(self) -> Dict[str, Any]:
        """
        Compute hashes of all critical files and store the manifest.

        Returns the manifest dict.
        """
        files: Dict[str, Dict[str, Any]] = {}

        for rel_path in CRITICAL_FILES:
            full = self._repo_root / rel_path
            if full.exists():
                files[rel_path] = {
                    "sha256": _sha256(full),
                    "size": full.stat().st_size,
                }
            else:
                logger.warning("Critical file missing: %s", rel_path)

        for rel_path in OPTIONAL_BINARIES:
            full = self._repo_root / rel_path
            if full.exists():
                files[rel_path] = {
                    "sha256": _sha256(full),
                    "size": full.stat().st_size,
                    "optional": True,
                }

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }

        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        self._manifest = manifest
        logger.info(
            "Integrity manifest generated: %d files at %s",
            len(files),
            self._manifest_path,
        )
        return manifest

    def verify_all(self) -> Dict[str, Any]:
        """
        Verify all critical files against the stored manifest.

        If no manifest exists, generates one (first run) and returns passed.

        Returns:
          {
            "passed": bool,
            "checked": int,
            "failures": [...],  # list of failed file paths
            "missing": [...],   # list of missing critical files
            "new_files": bool,  # True if manifest was just generated
          }
        """
        # Load or generate manifest
        if self._manifest is None:
            if self._manifest_path.exists():
                with open(self._manifest_path) as f:
                    self._manifest = json.load(f)
            else:
                self.generate_manifest()
                return {
                    "passed": True,
                    "checked": len(self._manifest.get("files", {})),
                    "failures": [],
                    "missing": [],
                    "new_files": True,
                }

        stored_files = self._manifest.get("files", {})
        failures: List[str] = []
        missing: List[str] = []
        checked = 0

        for rel_path, info in stored_files.items():
            full = self._repo_root / rel_path
            if not full.exists():
                if not info.get("optional"):
                    missing.append(rel_path)
                continue

            checked += 1
            current_hash = _sha256(full)
            if current_hash != info["sha256"]:
                failures.append(rel_path)
                logger.warning(
                    "Integrity MISMATCH: %s (expected=%s got=%s)",
                    rel_path,
                    info["sha256"][:12],
                    current_hash[:12],
                )

        passed = len(failures) == 0 and len(missing) == 0
        result = {
            "passed": passed,
            "checked": checked,
            "failures": failures,
            "missing": missing,
            "new_files": False,
        }

        if passed:
            logger.info("Integrity check passed: %d files verified", checked)
        else:
            logger.error(
                "Integrity check FAILED: %d failures, %d missing",
                len(failures),
                len(missing),
            )

        return result

    def verify_file(self, rel_path: str) -> bool:
        """Check a single file against the manifest."""
        if self._manifest is None:
            if self._manifest_path.exists():
                with open(self._manifest_path) as f:
                    self._manifest = json.load(f)
            else:
                return True  # no manifest yet — skip

        info = self._manifest.get("files", {}).get(rel_path)
        if not info:
            return True  # file not in manifest — skip

        full = self._repo_root / rel_path
        if not full.exists():
            return False

        return _sha256(full) == info["sha256"]

    def get_manifest(self) -> Optional[Dict[str, Any]]:
        """Return the current manifest, loading from disk if needed."""
        if self._manifest is None and self._manifest_path.exists():
            with open(self._manifest_path) as f:
                self._manifest = json.load(f)
        return self._manifest
