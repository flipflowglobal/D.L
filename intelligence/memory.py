"""
intelligence/memory.py — Async SQLite persistence for agent state.

Key-value store scoped per agent_id.  Uses aiosqlite for non-blocking
I/O so it integrates cleanly with the FastAPI / asyncio event loop.

Usage:
    from intelligence.memory import memory

    await memory.init_db()
    await memory.store("AUREON", "last_price", "3200.50")
    val = await memory.retrieve("AUREON", "last_price")  # "3200.50"
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger("aureon.memory")

DB_PATH = str(Path(__file__).parent.parent / "aureon_memory.db")


class Memory:
    """Async SQLite key-value store for agent state."""

    def __init__(self) -> None:
        self.db_path = DB_PATH

    async def init_db(self) -> None:
        """Create the memory table if it does not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    agent_id TEXT NOT NULL,
                    key      TEXT NOT NULL,
                    value    TEXT,
                    PRIMARY KEY (agent_id, key)
                )
            """)
            await db.commit()
        logger.debug("Memory DB ready: %s", self.db_path)

    async def store(self, agent_id: str, key: str, value: str) -> None:
        """
        Upsert *value* at (agent_id, key).

        Args:
            agent_id: agent namespace.
            key:      string key.
            value:    string value (caller must serialise complex types).
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO memory (agent_id, key, value) VALUES (?, ?, ?)",
                    (agent_id, key, str(value)),
                )
                await db.commit()
        except Exception as exc:
            logger.error("memory.store(%s, %s) failed: %s", agent_id, key, exc)

    async def retrieve(self, agent_id: str, key: str) -> Optional[str]:
        """
        Return the stored value for (agent_id, key), or None if absent.

        Args:
            agent_id: agent namespace.
            key:      string key.

        Returns:
            Stored string value, or None.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT value FROM memory WHERE agent_id = ? AND key = ?",
                    (agent_id, key),
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else None
        except Exception as exc:
            logger.error("memory.retrieve(%s, %s) failed: %s", agent_id, key, exc)
            return None

    async def delete(self, agent_id: str, key: str) -> None:
        """Delete a single key for *agent_id*."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM memory WHERE agent_id = ? AND key = ?",
                    (agent_id, key),
                )
                await db.commit()
        except Exception as exc:
            logger.error("memory.delete(%s, %s) failed: %s", agent_id, key, exc)

    async def list_keys(self, agent_id: str) -> list[str]:
        """Return all keys stored for *agent_id*."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT key FROM memory WHERE agent_id = ? ORDER BY key",
                    (agent_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [r[0] for r in rows]
        except Exception as exc:
            logger.error("memory.list_keys(%s) failed: %s", agent_id, exc)
            return []

    async def clear(self, agent_id: str) -> None:
        """Delete all keys for *agent_id*."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM memory WHERE agent_id = ?", (agent_id,)
                )
                await db.commit()
            logger.info("memory.clear: removed all keys for %s", agent_id)
        except Exception as exc:
            logger.error("memory.clear(%s) failed: %s", agent_id, exc)

    async def all(self, agent_id: str) -> dict[str, str]:
        """Return all (key, value) pairs for *agent_id* as a dict."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT key, value FROM memory WHERE agent_id = ? ORDER BY key",
                    (agent_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return {r[0]: r[1] for r in rows}
        except Exception as exc:
            logger.error("memory.all(%s) failed: %s", agent_id, exc)
            return {}


# ── Module-level singleton ────────────────────────────────────────────────────
memory = Memory()
