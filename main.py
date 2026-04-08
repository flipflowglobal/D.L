# main.py

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from intelligence.memory import memory
from intelligence.autonomy import loop


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
async def start_agent(agent_id: str):

    if loop.running is False:
        loop.running = True

    asyncio.create_task(loop.run(agent_id))

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
async def stop_agent():

    loop.running = False

    return JSONResponse(
        content={
            "status": "agent stopped"
        }
    )


# --------------------------------------------------
# MEMORY DEBUG ENDPOINT
# --------------------------------------------------

@app.get("/memory/{agent_id}/{key}")
async def get_memory(agent_id: str, key: str):

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
