"""
core/state_manager.py — Persistent State Snapshot & Recovery
=============================================================

Provides continuous SQLite-based state snapshots of:
  - All registered trading agents (config, status, metrics)
  - Kernel health metadata
  - Watchdog reports

Recovery flow:
  1. On startup, load the latest snapshot from the DB
  2. Reconstruct agent registry from the snapshot
  3. Continue operation from the recovered state

Architecture:
  StateManager (singleton)
    ├── snapshot()          → save current system state to DB
    ├── recover()           → load last state and rebuild agents
    ├── get_history()       → list recent snapshots
    └── cleanup()           → purge old snapshots (keep last N)

DB Schema:
  snapshots(id, timestamp, data JSON, checksum TEXT)

Usage:
  from core.state_manager import state_manager
  await state_manager.init_db()
  snap = await state_manager.snapshot()
  state = await state_manager.recover()
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger("aureon.state_manager")

# ── Configuration ─────────────────────────────────────────────────────────────

DB_PATH = Path(
    os.getenv(
        "STATE_DB_PATH",
        str(Path(__file__).resolve().parent.parent / "aureon_state.db"),
    )
)
MAX_SNAPSHOTS = int(os.getenv("STATE_MAX_SNAPSHOTS", "100"))


def _checksum(data: str) -> str:
    """SHA-256 checksum of serialised state data."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class StateManager:
    """
    Persistent state snapshot and recovery manager.

    Uses an async SQLite database to store JSON snapshots of the full
    system state at configurable intervals. Supports recovery of agent
    state after crashes.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or DB_PATH
        self._initialized = False

    async def init_db(self) -> None:
        """Create the snapshots table if it doesn't exist."""
        if self._initialized:
            return

        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT    NOT NULL,
                    data      TEXT    NOT NULL,
                    checksum  TEXT    NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_ts
                ON snapshots(timestamp DESC)
                """
            )
            await db.commit()

        self._initialized = True
        logger.info("StateManager initialised — db=%s", self.db_path)

    async def snapshot(self) -> Dict[str, Any]:
        """
        Capture and persist the current system state.

        Returns the snapshot metadata (timestamp, agent_count, checksum).
        """
        await self.init_db()

        # ── Gather state ──────────────────────────────────────────────────────
        state = self._gather_state()
        ts = datetime.now(timezone.utc).isoformat()
        state["timestamp"] = ts

        data_json = json.dumps(state, default=str)
        cs = _checksum(data_json)

        # ── Persist ───────────────────────────────────────────────────────────
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                "INSERT INTO snapshots (timestamp, data, checksum) VALUES (?, ?, ?)",
                (ts, data_json, cs),
            )
            await db.commit()

        # ── Cleanup old snapshots ─────────────────────────────────────────────
        await self._cleanup()

        result = {
            "timestamp": ts,
            "agent_count": state.get("agent_count", 0),
            "checksum": cs,
            "db_path": str(self.db_path),
        }
        logger.debug("Snapshot saved: %s", result)
        return result

    async def recover(self) -> Optional[Dict[str, Any]]:
        """
        Load the most recent valid snapshot.

        Returns the parsed state dict, or None if no snapshot exists.
        Validates the checksum to detect corruption.
        """
        await self.init_db()

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT data, checksum FROM snapshots ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            logger.info("No snapshots found — starting fresh")
            return None

        data_json = row["data"]
        stored_cs = row["checksum"]
        computed_cs = _checksum(data_json)

        if stored_cs != computed_cs:
            logger.error(
                "Snapshot checksum mismatch! stored=%s computed=%s",
                stored_cs,
                computed_cs,
            )
            return None

        state = json.loads(data_json)
        logger.info(
            "Recovered state from %s — %d agents",
            state.get("timestamp", "?"),
            state.get("agent_count", 0),
        )
        return state

    async def recover_agents(self) -> int:
        """
        Recover trading agents from the latest snapshot.

        Creates new agents in the registry with the saved configs.
        Returns the number of agents recovered.
        """
        state = await self.recover()
        if not state:
            return 0

        agents_data = state.get("agents", [])
        if not agents_data:
            return 0

        from intelligence.trading_agent import (
            TradingAgentConfig,
            Strategy,
            Chain,
            Token,
            registry,
        )

        recovered = 0
        for agent_info in agents_data:
            try:
                config = TradingAgentConfig(
                    name=agent_info.get("name", "Recovered"),
                    strategy=Strategy(
                        agent_info.get("strategy", "arb")
                    ),
                    chain=Chain(
                        agent_info.get("chain", "ethereum")
                    ),
                    token=Token(
                        agent_info.get("token", "ETH")
                    ),
                    initial_capital=agent_info.get(
                        "initial_capital", 10000.0
                    ),
                    trade_size_eth=agent_info.get(
                        "trade_size_eth", 0.05
                    ),
                    min_profit_usd=agent_info.get(
                        "min_profit_usd", 2.0
                    ),
                    scan_interval=agent_info.get("scan_interval", 30),
                    dry_run=agent_info.get("dry_run", True),
                )
                registry.create(config)
                recovered += 1
            except Exception as exc:
                logger.warning(
                    "Failed to recover agent %s: %s",
                    agent_info.get("name", "?"),
                    exc,
                )

        logger.info("Recovered %d/%d agents from snapshot", recovered, len(agents_data))
        return recovered

    async def get_history(
        self, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Return metadata for the N most recent snapshots."""
        await self.init_db()

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, timestamp, checksum FROM snapshots "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()

        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "checksum": row["checksum"],
            }
            for row in rows
        ]

    async def _cleanup(self) -> None:
        """Remove old snapshots beyond MAX_SNAPSHOTS."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                """
                DELETE FROM snapshots
                WHERE id NOT IN (
                    SELECT id FROM snapshots
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (MAX_SNAPSHOTS,),
            )
            await db.commit()

    def _gather_state(self) -> Dict[str, Any]:
        """Collect current system state from all components."""
        state: Dict[str, Any] = {
            "version": "1.0",
            "agents": [],
            "agent_count": 0,
        }

        try:
            from intelligence.trading_agent import registry

            agents_list = registry.list_all()
            state["agents"] = agents_list
            state["agent_count"] = len(agents_list)

            # Add detailed performance for running agents
            detailed = []
            for info in agents_list:
                agent = registry.get(info.get("agent_id", ""))
                if agent:
                    detailed.append(agent.performance())
            state["agent_performance"] = detailed
        except Exception as exc:
            logger.warning("Could not gather agent state: %s", exc)

        return state


# ── Module-level singleton ────────────────────────────────────────────────────

state_manager = StateManager()
