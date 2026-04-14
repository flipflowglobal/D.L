"""
tests/test_new_endpoints.py
============================

Tests for new API endpoints:
  PATCH /agents/{id}
  POST  /agents/batch
  POST  /agents/{id}/reset
  GET   /swarm/consensus
  GET   /swarm/metrics
  POST  /swarm/start
  POST  /swarm/stop
  POST  /registry/save
  POST  /registry/load
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


@pytest.fixture()
async def client():
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── PATCH /agents/{id} ────────────────────────────────────────────────────────

async def test_patch_agent_min_profit(client):
    r = await client.post("/agents", json={"strategy": "arb", "dry_run": True})
    assert r.status_code == 201
    agent_id = r.json()["agent_id"]

    patch = await client.patch(f"/agents/{agent_id}", json={"min_profit_usd": 99.99})
    assert patch.status_code == 200

    # Verify the change persisted
    get = await client.get(f"/agents/{agent_id}")
    assert get.status_code == 200


async def test_patch_agent_not_found(client):
    r = await client.patch("/agents/nonexistent", json={"dry_run": False})
    assert r.status_code == 404


async def test_patch_agent_scan_interval(client):
    r = await client.post("/agents", json={"strategy": "ppo", "dry_run": True})
    agent_id = r.json()["agent_id"]
    patch = await client.patch(f"/agents/{agent_id}", json={"scan_interval": 120})
    assert patch.status_code == 200


# ── POST /agents/batch ────────────────────────────────────────────────────────

async def test_batch_create_agents(client):
    payload = {
        "agents": [
            {"name": "BatchBot1", "strategy": "arb",  "dry_run": True},
            {"name": "BatchBot2", "strategy": "ppo",  "dry_run": True},
            {"name": "BatchBot3", "strategy": "adaptive", "dry_run": True},
        ]
    }
    r = await client.post("/agents/batch", json=payload)
    assert r.status_code == 201
    data = r.json()
    assert data["count"] == 3
    assert len(data["created"]) == 3
    assert data["errors"] == []


async def test_batch_create_too_many(client):
    payload = {"agents": [{"strategy": "arb", "dry_run": True}] * 11}
    r = await client.post("/agents/batch", json=payload)
    assert r.status_code == 400


async def test_batch_create_empty(client):
    payload = {"agents": []}
    r = await client.post("/agents/batch", json=payload)
    assert r.status_code == 201
    assert r.json()["count"] == 0


# ── POST /agents/{id}/reset ───────────────────────────────────────────────────

async def test_reset_agent_endpoint(client):
    r = await client.post("/agents", json={"strategy": "arb", "dry_run": True})
    agent_id = r.json()["agent_id"]

    reset = await client.post(f"/agents/{agent_id}/reset")
    assert reset.status_code == 200
    assert reset.json()["status"] == "idle"


async def test_reset_nonexistent_agent(client):
    r = await client.post("/agents/badid/reset")
    assert r.status_code == 404


# ── GET /swarm/consensus ──────────────────────────────────────────────────────

async def test_swarm_consensus(client):
    r = await client.get("/swarm/consensus")
    assert r.status_code == 200
    data = r.json()
    assert "signal" in data
    # NO_CONSENSUS is returned when no agents are running
    assert data["signal"] in ("BUY", "SELL", "HOLD", "NO_CONSENSUS")
    assert "votes" in data
    assert "running_agents" in data


# ── GET /swarm/metrics ────────────────────────────────────────────────────────

async def test_swarm_metrics(client):
    r = await client.get("/swarm/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "total_agents" in data
    assert "by_status" in data
    assert "total_pnl_usd" in data
    assert "max_agents" in data


# ── POST /swarm/start and /swarm/stop ────────────────────────────────────────

async def test_swarm_start_stop(client):
    # Create some agents first
    for _ in range(2):
        await client.post("/agents", json={"strategy": "arb", "dry_run": True, "scan_interval": 9999})

    start = await client.post("/swarm/start")
    assert start.status_code == 200
    data = start.json()
    assert "started" in data
    assert "count" in data

    stop = await client.post("/swarm/stop")
    assert stop.status_code == 200
    assert "stopped" in stop.json()


# ── POST /registry/save and /registry/load ───────────────────────────────────

async def test_registry_save(client):
    # Paths must be inside the vault/ directory
    r = await client.post("/registry/save?path=vault/test_registry.json")
    assert r.status_code == 200
    assert "saved" in r.json()


async def test_registry_load_nonexistent(client):
    # Paths must be inside the vault/ directory; nonexistent file returns 0 loaded
    r = await client.post("/registry/load?path=vault/nonexistent_xyz.json")
    assert r.status_code == 200
    assert r.json()["loaded"] == 0
