"""
main.py — AUREON FastAPI entry-point.

Exposes HTTP endpoints for controlling the autonomous agent loop,
querying persisted memory, and health-checking the system.

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

# ── uvloop: 2-4x faster event loop (Linux/macOS only) ─────────────────────────
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass  # uvloop not available (Windows / Android) — falls back to asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from intelligence.memory import memory
from intelligence.autonomy import loop

logger = logging.getLogger("aureon.main")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


# ── Lifespan: init / teardown ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup; ensure loop is stopped on shutdown."""
    await memory.init_db()
    logger.info("Memory database initialized")
    logger.info("Cognitive system online")
    yield
    # Graceful shutdown: signal the agent loop to stop
    if loop.running:
        loop.running = False
        logger.info("Agent loop signalled to stop on shutdown")


# ── Application ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AUREON Cognitive System",
    description="Autonomous cognitive agent for the OnTheDL architecture.",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/", summary="System identity")
async def root() -> dict:
    """Return system name and current run-state."""
    return {
        "system": "AUREON",
        "status": "running" if loop.running else "idle",
    }


@app.get("/health", summary="Health check")
async def health() -> dict:
    """Lightweight liveness probe — always 200 if the server is up."""
    return {"status": "ok"}


@app.get("/status", summary="Agent loop status")
async def status() -> dict:
    """Return whether the autonomous agent loop is currently active."""
    return {"agent_loop_running": loop.running}


@app.post("/aureon/start", summary="Start the agent loop")
async def start_agent(agent_id: str) -> JSONResponse:
    """
    Launch the autonomous trading/reasoning loop for *agent_id*.

    Returns 409 if the loop is already running.

    Args:
        agent_id: non-empty string namespace for this agent instance.
    """
    if not agent_id or not agent_id.strip():
        raise HTTPException(status_code=422, detail="agent_id must be a non-empty string")

    if loop.running:
        raise HTTPException(
            status_code=409,
            detail=f"Agent loop is already running. POST /aureon/stop first.",
        )

    loop.running = True
    asyncio.create_task(loop.run(agent_id))
    logger.info("Agent loop started for agent_id=%s", agent_id)

    return JSONResponse(
        status_code=200,
        content={"status": "agent started", "agent_id": agent_id},
    )


@app.post("/aureon/stop", summary="Stop the agent loop")
async def stop_agent() -> JSONResponse:
    """Signal the autonomous loop to halt after its current iteration."""
    if not loop.running:
        return JSONResponse(
            status_code=200,
            content={"status": "agent was not running"},
        )

    loop.running = False
    logger.info("Agent loop stop requested")

    return JSONResponse(
        status_code=200,
        content={"status": "agent stopped"},
    )


@app.get("/memory/{agent_id}", summary="List all memory keys for an agent")
async def list_memory(agent_id: str) -> dict:
    """Return all key→value pairs stored for *agent_id*."""
    if not agent_id or not agent_id.strip():
        raise HTTPException(status_code=422, detail="agent_id must be a non-empty string")

    data = await memory.all(agent_id)
    return {"agent_id": agent_id, "entries": data}


@app.get("/memory/{agent_id}/{key}", summary="Read a single memory value")
async def get_memory(agent_id: str, key: str) -> dict:
    """
    Return the stored value for (agent_id, key).

    Returns ``{"value": null}`` when the key does not exist.
    """
    value = await memory.retrieve(agent_id, key)
    return {"agent_id": agent_id, "key": key, "value": value}


@app.delete("/memory/{agent_id}/{key}", summary="Delete a memory entry")
async def delete_memory(agent_id: str, key: str) -> dict:
    """Delete a single persisted key for *agent_id*."""
    await memory.delete(agent_id, key)
    return {"agent_id": agent_id, "key": key, "deleted": True}


@app.delete("/memory/{agent_id}", summary="Clear all memory for an agent")
async def clear_memory(agent_id: str) -> dict:
    """Remove every persisted key for *agent_id*."""
    await memory.clear(agent_id)
    return {"agent_id": agent_id, "cleared": True}
