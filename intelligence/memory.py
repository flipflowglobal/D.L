import aiosqlite
import os
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "aureon_memory.db")


class Memory:
    """
    Async SQLite-backed key-value store for agent state.

    Uses a single persistent connection (lazy-initialized) to avoid
    opening a new connection on every store/retrieve call.
    """

    def __init__(self):
        self.db_path = DB_PATH
        self._db: Optional[aiosqlite.Connection] = None

    async def _get_db(self) -> aiosqlite.Connection:
        """Return the persistent connection, creating it if needed."""
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    agent_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    PRIMARY KEY (agent_id, key)
                )
            """)
            await self._db.commit()
        return self._db

    async def init_db(self):
        """Ensure the database and table exist (called once at startup)."""
        await self._get_db()

    async def store(self, agent_id: str, key: str, value: str):
        db = await self._get_db()
        await db.execute(
            "INSERT OR REPLACE INTO memory (agent_id, key, value) VALUES (?, ?, ?)",
            (agent_id, key, value)
        )
        await db.commit()

    async def retrieve(self, agent_id: str, key: str):
        db = await self._get_db()
        async with db.execute(
            "SELECT value FROM memory WHERE agent_id = ? AND key = ?",
            (agent_id, key)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def close(self):
        """Close the persistent connection (called at shutdown)."""
        if self._db is not None:
            await self._db.close()
            self._db = None


memory = Memory()
