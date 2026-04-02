"""
Tests for the FastAPI cognitive agent server (main.py).
Uses httpx AsyncClient — no live server process needed.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
async def client():
    from httpx import AsyncClient, ASGITransport
    from main import app
    from intelligence.memory import memory

    await memory.init_db()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.anyio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"health": "ok"}


@pytest.mark.anyio
async def test_root(client):
    r = await client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["system"] == "AUREON"
    assert data["status"] == "running"


@pytest.mark.anyio
async def test_status(client):
    r = await client.get("/status")
    assert r.status_code == 200
    assert "agent_loop_running" in r.json()


@pytest.mark.anyio
async def test_memory_missing_key(client):
    r = await client.get("/memory/test_agent/nonexistent_key")
    assert r.status_code == 200
    assert r.json()["value"] is None
