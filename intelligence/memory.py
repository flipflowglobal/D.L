import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "aureon_memory.db")


class Memory:
    def __init__(self):
        self.db_path = DB_PATH

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    agent_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    PRIMARY KEY (agent_id, key)
                )
            """)
            await db.commit()

    async def store(self, agent_id: str, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO memory (agent_id, key, value) VALUES (?, ?, ?)",
                (agent_id, key, value)
            )
            await db.commit()

    async def retrieve(self, agent_id: str, key: str):
        async with aiosqlite.connect(self.db_path) as db:
            # Auto-create table on first access (handles test environments where
            # lifespan / init_db() may not have been called yet).
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    agent_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    PRIMARY KEY (agent_id, key)
                )
            """)
            await db.commit()
            async with db.execute(
                "SELECT value FROM memory WHERE agent_id = ? AND key = ?",
                (agent_id, key)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None


memory = Memory()
