"""
database.py — Async SQLite persistence for agents and task records.

Manages two tables:
  * agents — registered agent metadata
  * tasks  — dispatched task payloads and their results

Usage:
    from database import db

    await db.initialize()
    await db.add_agent("AUREON", "Aureon", "trader")
    await db.add_task("AUREON", "arb_scan", '{"pair": "ETH/USDC"}')
    await db.update_task("task-id", status="done", result='{"profit": 5.2}')
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger("aureon.database")

DB_PATH = Path(__file__).parent / "aureon_persistence.db"


class Database:
    """Async SQLite store for agent registrations and task dispatch records."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path

    # ── Schema init ───────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create all tables if they do not exist."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    id         TEXT PRIMARY KEY,
                    name       TEXT NOT NULL,
                    type       TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id         TEXT PRIMARY KEY,
                    agent_id   TEXT NOT NULL,
                    task_type  TEXT NOT NULL,
                    payload    TEXT,
                    status     TEXT NOT NULL DEFAULT 'pending',
                    result     TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.commit()
        logger.debug("Database ready: %s", self.db_path)

    # ── Agent operations ──────────────────────────────────────────────────────

    async def add_agent(
        self, agent_id: str, name: str, agent_type: str
    ) -> None:
        """
        Insert a new agent record.  Silently ignores duplicate agent_id.

        Args:
            agent_id:   unique identifier (e.g. "AUREON").
            name:       human-readable name.
            agent_type: role label (e.g. "trader", "monitor").
        """
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    "INSERT OR IGNORE INTO agents (id, name, type) VALUES (?, ?, ?)",
                    (agent_id, name, agent_type),
                )
                await conn.commit()
        except Exception as exc:
            logger.error("add_agent(%s) failed: %s", agent_id, exc)

    async def get_agents(self) -> list[dict[str, Any]]:
        """Return all registered agents as a list of dicts."""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute("SELECT * FROM agents ORDER BY created_at") as cur:
                    rows = await cur.fetchall()
                return [dict(row) for row in rows]
        except Exception as exc:
            logger.error("get_agents() failed: %s", exc)
            return []

    async def get_agent(self, agent_id: str) -> Optional[dict[str, Any]]:
        """Return a single agent dict, or None if not found."""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM agents WHERE id = ?", (agent_id,)
                ) as cur:
                    row = await cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.error("get_agent(%s) failed: %s", agent_id, exc)
            return None

    # ── Task operations ───────────────────────────────────────────────────────

    async def add_task(
        self,
        agent_id: str,
        task_type: str,
        payload: str = "",
        task_id: Optional[str] = None,
    ) -> str:
        """
        Insert a new task record and return its id.

        Args:
            agent_id:  owning agent.
            task_type: category label (e.g. "arb_scan", "buy").
            payload:   JSON-serialised arguments string.
            task_id:   override auto-generated UUID (optional).

        Returns:
            The task id string.
        """
        tid = task_id or str(uuid.uuid4())
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO tasks (id, agent_id, task_type, payload, status)
                    VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (tid, agent_id, task_type, payload),
                )
                await conn.commit()
        except Exception as exc:
            logger.error("add_task(%s, %s) failed: %s", agent_id, task_type, exc)
        return tid

    async def update_task(
        self,
        task_id: str,
        status: str,
        result: str = "",
    ) -> None:
        """
        Update the status and result of an existing task.

        Args:
            task_id: UUID of the task to update.
            status:  new status string (e.g. "done", "error").
            result:  JSON-serialised result payload.
        """
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, result = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (status, result, task_id),
                )
                await conn.commit()
        except Exception as exc:
            logger.error("update_task(%s) failed: %s", task_id, exc)

    async def get_tasks(
        self, agent_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return the most recent *limit* tasks for *agent_id* (newest first)."""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    """
                    SELECT * FROM tasks WHERE agent_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (agent_id, limit),
                ) as cur:
                    rows = await cur.fetchall()
                return [dict(row) for row in rows]
        except Exception as exc:
            logger.error("get_tasks(%s) failed: %s", agent_id, exc)
            return []

    async def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        """Return a single task dict, or None if not found."""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM tasks WHERE id = ?", (task_id,)
                ) as cur:
                    row = await cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.error("get_task(%s) failed: %s", task_id, exc)
            return None


# ── Module-level singleton ────────────────────────────────────────────────────
db = Database()
