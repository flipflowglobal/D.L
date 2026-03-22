import aiosqlite
import json
from pathlib import Path

DB_PATH = Path("aureon_persistence.db")

class Database:

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def initialize(self):

        async with aiosqlite.connect(self.db_path) as db:

            await db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                agent_id TEXT,
                task_type TEXT,
                payload TEXT,
                status TEXT,
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            await db.commit()

    async def add_agent(self, agent_id, name, agent_type):

        async with aiosqlite.connect(self.db_path) as db:

            await db.execute(
                "INSERT INTO agents (id,name,type) VALUES (?,?,?)",
                (agent_id,name,agent_type)
            )

            await db.commit()

    async def get_agents(self):

        async with aiosqlite.connect(self.db_path) as db:

            db.row_factory = aiosqlite.Row

            async with db.execute("SELECT * FROM agents") as cursor:
                rows = await cursor.fetchall()

            return [dict(row) for row in rows]


DB = Database(DB_PATH)
