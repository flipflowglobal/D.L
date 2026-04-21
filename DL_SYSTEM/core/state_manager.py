"""
DL_SYSTEM/core/state_manager.py — Persistent task state store.

Reads and writes DL_SYSTEM/data/state.json.  The state file contains a
list of task dicts, each with at least ``id``, ``name``, ``type``, and
optionally ``last_result``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("dl_system.state_manager")

_DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
STATE_FILE = os.path.join(_DATA_DIR, "state.json")

_DEFAULT_STATE: dict[str, Any] = {"tasks": []}


class StateManager:
    """JSON-backed task state store."""

    def __init__(self) -> None:
        os.makedirs(_DATA_DIR, exist_ok=True)
        if not os.path.exists(STATE_FILE):
            self._write(_DEFAULT_STATE)
            logger.debug("Created new state file: %s", STATE_FILE)

    # ── Internal I/O ──────────────────────────────────────────────────────────

    def _read(self) -> dict[str, Any]:
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("State file unreadable (%s) — using default", exc)
            return dict(_DEFAULT_STATE)

    def _write(self, data: dict[str, Any]) -> None:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """Return the full state dict."""
        return self._read()

    def save(self, data: dict[str, Any]) -> None:
        """Overwrite the state file with *data*."""
        self._write(data)

    def get_tasks(self) -> list[dict[str, Any]]:
        """Return the list of task dicts."""
        return self._read().get("tasks", [])

    def update_task(self, task_id: str, result: Any) -> None:
        """Persist *result* on the task matching *task_id*."""
        data = self._read()
        updated = False
        for task in data.get("tasks", []):
            if task.get("id") == task_id:
                task["last_result"] = result
                updated = True
                break
        if not updated:
            logger.warning("update_task: task_id '%s' not found", task_id)
        self._write(data)
