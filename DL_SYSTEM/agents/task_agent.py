"""
DL_SYSTEM/agents/task_agent.py — Task dispatcher for DL_SYSTEM.

Routes each task dict to the appropriate integration handler based on
the task ``type`` field.  Unknown types are logged and skipped.
"""

from __future__ import annotations

import logging
from typing import Any

from integrations.galxe  import run_galxe_task
from integrations.layer3 import run_layer3_task

logger = logging.getLogger("dl_system.task_agent")


class TaskAgent:
    """Dispatches task dicts to integration handlers."""

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        Execute *task* using the appropriate integration handler.

        Args:
            task: dict with at least ``type`` (str) and ``id`` (str).

        Returns:
            Result dict with at minimum a ``status`` key.
        """
        task_type = task.get("type", "")
        task_name = task.get("name", task_type)

        try:
            if task_type == "galxe":
                return run_galxe_task(task)
            elif task_type == "layer3":
                return run_layer3_task(task)
            else:
                logger.warning("Unknown task type '%s' for task '%s'", task_type, task_name)
                return {"status": "skipped", "reason": f"unknown type '{task_type}'"}
        except Exception as exc:
            logger.error("Task '%s' (type=%s) raised: %s", task_name, task_type, exc)
            return {"status": "error", "error": str(exc)}
