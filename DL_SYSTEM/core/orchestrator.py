"""
DL_SYSTEM/core/orchestrator.py — Task orchestration loop.

Loads pending tasks from StateManager, dispatches each to the TaskAgent,
and persists the results back to state.
"""

from __future__ import annotations

import logging

from agents.task_agent import TaskAgent
from core.state_manager import StateManager
from core.logger import log_event

logger = logging.getLogger("dl_system.orchestrator")


class Orchestrator:
    """Coordinates one full execution cycle: load → dispatch → persist."""

    def __init__(self) -> None:
        self.state      = StateManager()
        self.task_agent = TaskAgent()

    def run_cycle(self) -> None:
        """Execute all pending tasks and log results."""
        logger.info("Starting execution cycle")
        tasks = self.state.get_tasks()

        if not tasks:
            logger.info("No pending tasks — cycle complete")
            return

        for task in tasks:
            task_name = task.get("name", "unnamed")
            logger.info("Executing: %s", task_name)
            try:
                result = self.task_agent.execute(task)
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
                logger.error("Task '%s' raised: %s", task_name, exc)

            log_event({"task": task_name, "result": result})
            self.state.update_task(task["id"], result)

        logger.info("Cycle complete (%d tasks)", len(tasks))
