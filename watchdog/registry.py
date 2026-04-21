"""
watchdog/registry.py — Agent registry and file discovery.

Responsibilities:
  1. Discover every .py file in the repository and spawn one FileAgent per file.
  2. Maintain a live registry of all active agents (keyed by agent_id).
  3. Provide lookup, health snapshot, and removal APIs to the kernel.

The registry does NOT start agents — that is the kernel's responsibility.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from watchdog.agents.base          import WatchdogAgent
from watchdog.agents.file_agent    import FileAgent
from watchdog.event_bus            import EventBus

logger = logging.getLogger("watchdog.registry")

_REPO_ROOT = Path(__file__).parent.parent

# Directories and patterns to skip when discovering Python files
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "env",
    "node_modules", ".mypy_cache", ".pytest_cache",
    "dist", "build", "site-packages",
})

_SKIP_FILES = frozenset({
    # Generated / compiled
    "*.pyc", "*.pyo",
})


def _should_skip(path: Path, repo_root: Path) -> bool:
    """Return True if *path* should be excluded from monitoring."""
    parts = path.relative_to(repo_root).parts
    return any(p in _SKIP_DIRS for p in parts)


def discover_python_files(repo_root: Path = _REPO_ROOT) -> List[Path]:
    """
    Recursively find all .py files under *repo_root*, excluding
    virtual-env and cache directories.
    """
    found: List[Path] = []
    for p in sorted(repo_root.rglob("*.py")):
        if not _should_skip(p, repo_root):
            found.append(p)
    logger.info("Discovered %d Python files under %s", len(found), repo_root)
    return found


class AgentRegistry:
    """
    Central registry for all active watchdog agents.

    Agents are registered with ``register()`` and can be looked up
    by ``agent_id``.  The registry also exposes an iterator so the
    kernel can start/stop all agents in a single loop.
    """

    def __init__(self) -> None:
        self._agents: Dict[str, WatchdogAgent] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, agent: WatchdogAgent) -> None:
        """Add *agent* to the registry (overwrites if same agent_id)."""
        if agent.agent_id in self._agents:
            logger.warning("Replacing existing agent: %s", agent.agent_id)
        self._agents[agent.agent_id] = agent
        logger.debug("Registered agent: %s", agent.agent_id)

    def unregister(self, agent_id: str) -> Optional[WatchdogAgent]:
        """Remove and return the agent with *agent_id*, or None."""
        agent = self._agents.pop(agent_id, None)
        if agent:
            logger.debug("Unregistered agent: %s", agent_id)
        return agent

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, agent_id: str) -> Optional[WatchdogAgent]:
        return self._agents.get(agent_id)

    def __iter__(self) -> Iterator[WatchdogAgent]:
        return iter(list(self._agents.values()))

    def __len__(self) -> int:
        return len(self._agents)

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def health_snapshot(self) -> List[dict]:
        """
        Return a list of status dicts, one per registered agent.
        Each dict is safe to serialise as JSON.
        """
        snapshots = []
        for agent in self._agents.values():
            try:
                snapshots.append(agent.status)
            except Exception as exc:
                snapshots.append({"agent_id": agent.agent_id, "error": str(exc)})
        return snapshots

    def counts_by_severity(self) -> dict:
        """Return {severity_name: count} across all last-known events."""
        counts: dict = {}
        for agent in self._agents.values():
            sev = agent.status.get("last_severity")
            if sev:
                counts[sev] = counts.get(sev, 0) + 1
        return counts

    # ── Factory: create one FileAgent per discovered .py file ─────────────────

    @classmethod
    def build_file_agents(
        cls,
        bus:       EventBus,
        repo_root: Path = _REPO_ROOT,
        interval:  float = 15.0,
    ) -> "AgentRegistry":
        """
        Discover all Python files and create a pre-populated registry
        containing one FileAgent per file.
        """
        registry = cls()
        files = discover_python_files(repo_root)
        for path in files:
            agent = FileAgent(path=path, repo_root=repo_root, bus=bus, interval=interval)
            registry.register(agent)
        logger.info("Built FileAgent registry: %d agents", len(registry))
        return registry
