# main.py

import asyncio
import sys
from contextlib import asynccontextmanager

# ── uvloop: 2–4× faster event loop (Linux/macOS only) ────────────────────────
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass  # uvloop not available (Windows / Android) — fall back to asyncio

import logging
import os

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from intelligence.memory import memory
from intelligence.autonomy import loop

_logger = logging.getLogger("aureon.api")

# ── Module-level task tracking ────────────────────────────────────────────────
_agent_task: asyncio.Task | None = None

# ── API key authentication ────────────────────────────────────────────────────
_API_KEY = os.getenv("AUREON_API_KEY", "")

async def _require_api_key(request: Request):
    """Validate X-API-Key header if AUREON_API_KEY is configured."""
    if not _API_KEY:
        return  # no key configured — allow all (dev mode)
    provided = request.headers.get("X-API-Key", "")
    if provided != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# --------------------------------------------------
# LIFESPAN — replaces deprecated @app.on_event
# --------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await memory.init_db()
    print("[AUREON] Memory database initialized")
    print("[AUREON] Cognitive system online")
    yield


# --------------------------------------------------
# CREATE FASTAPI APPLICATION
# --------------------------------------------------

app = FastAPI(
    title="AUREON Cognitive System",
    description="Autonomous cognitive agent for OnTheDL architecture",
    version="1.0",
    lifespan=lifespan,
)


# --------------------------------------------------
# ROOT ENDPOINT
# --------------------------------------------------

@app.get("/")
async def root():
    return {
        "system": "AUREON",
        "status": "running"
    }


# --------------------------------------------------
# SYSTEM STATUS
# --------------------------------------------------

@app.get("/status")
async def status():
    return {
        "agent_loop_running": loop.running
    }


# --------------------------------------------------
# START AUTONOMOUS AGENT
# --------------------------------------------------

@app.post("/aureon/start")
async def start_agent(agent_id: str, _auth=Depends(_require_api_key)):
    global _agent_task

    if loop.running:
        return JSONResponse(content={"status": "already running", "agent_id": agent_id})

    loop.running = True
    _agent_task = asyncio.create_task(loop.run(agent_id))

    def _on_done(task: asyncio.Task):
        try:
            exc = task.exception()
            if exc:
                _logger.error("Agent task failed: %s", exc)
        except asyncio.CancelledError:
            pass

    _agent_task.add_done_callback(_on_done)

    return JSONResponse(
        content={
            "status": "agent started",
            "agent_id": agent_id
        }
    )


# --------------------------------------------------
# STOP AUTONOMOUS AGENT
# --------------------------------------------------

@app.post("/aureon/stop")
async def stop_agent(_auth=Depends(_require_api_key)):
    global _agent_task

    loop.running = False

    if _agent_task and not _agent_task.done():
        _agent_task.cancel()
        _agent_task = None

    return JSONResponse(
        content={
            "status": "agent stopped"
        }
    )


# --------------------------------------------------
# MEMORY DEBUG ENDPOINT
# --------------------------------------------------

@app.get("/memory/{agent_id}/{key}")
async def get_memory(agent_id: str, key: str, _auth=Depends(_require_api_key)):

    value = await memory.retrieve(agent_id, key)

    return {
        "agent_id": agent_id,
        "key": key,
        "value": value
    }


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}
