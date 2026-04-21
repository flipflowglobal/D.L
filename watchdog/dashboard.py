"""
watchdog/dashboard.py — FastAPI health dashboard for the watchdog kernel.

Mounts at /watchdog and exposes:
  GET  /watchdog/health       — full JSON snapshot (used by monitoring)
  GET  /watchdog/agents       — list of all agents + last event
  GET  /watchdog/agents/{id}  — single agent status
  GET  /watchdog/events       — last N events from the event bus
  GET  /watchdog/heals        — healing strategy records
  POST /watchdog/heal/{id}    — manually trigger heal on an agent

The dashboard is designed to be mounted inside an existing FastAPI app:

    from watchdog.dashboard import router as watchdog_router
    app.include_router(watchdog_router)

Or run standalone:

    uvicorn watchdog.dashboard:app --port 8020
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Deque, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from watchdog.event_bus  import EventSeverity, EventType, WatchdogEvent, event_bus
from watchdog.kernel     import kernel
from watchdog.mind.sync  import shared_mind

logger = logging.getLogger("watchdog.dashboard")

# Rolling buffer of last 500 events (populated by the event bus subscriber)
_MAX_EVENTS = 500
_recent_events: Deque[dict] = deque(maxlen=_MAX_EVENTS)


def _event_to_dict(event: WatchdogEvent) -> dict:
    return {
        "event_type": event.event_type.name,
        "severity":   event.severity.name,
        "agent_id":   event.agent_id,
        "source":     event.source,
        "message":    event.message,
        "wall_time":  event.wall_time,
        "details":    event.details,
    }


async def _capture_event(event: WatchdogEvent) -> None:
    """EventBus subscriber that records recent events for the dashboard."""
    _recent_events.append(_event_to_dict(event))


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/watchdog", tags=["watchdog"])


@router.get("/health")
async def get_health() -> JSONResponse:
    """
    Full watchdog health snapshot.

    Returns kernel state, per-agent statuses, bus stats, and heal records.
    """
    snapshot = kernel.health_snapshot()
    # Determine overall status
    sev = snapshot.get("severity_counts", {})
    if sev.get("CRITICAL", 0) > 0:
        overall = "CRITICAL"
    elif sev.get("WARNING", 0) > 0:
        overall = "DEGRADED"
    else:
        overall = "OK"

    return JSONResponse(
        content={"status": overall, **snapshot},
        status_code=200 if overall == "OK" else 207,
    )


@router.get("/agents")
async def list_agents() -> List[dict]:
    """List all registered agents and their last-known status."""
    return kernel.registry.health_snapshot()


@router.get("/agents/{agent_id:path}")
async def get_agent(agent_id: str) -> dict:
    """Get the status of a specific agent by its agent_id."""
    agent = kernel.registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return agent.status


@router.get("/events")
async def get_events(
    n: int = 100,
    severity: Optional[str] = None,
) -> List[dict]:
    """
    Return the last *n* recorded events (default 100, max 500).

    Optional filter: ?severity=CRITICAL | WARNING | INFO | HEALED
    """
    n = min(n, _MAX_EVENTS)
    events = list(_recent_events)[-n:]
    if severity:
        sev_upper = severity.upper()
        events = [e for e in events if e["severity"] == sev_upper]
    return events


@router.get("/heals")
async def get_heals() -> List[dict]:
    """Return the healing strategy's per-agent records."""
    return kernel.strategy.snapshot()


@router.post("/heal/{agent_id:path}")
async def trigger_heal(agent_id: str) -> dict:
    """
    Manually trigger a heal on the specified agent.

    The most recent event for the agent is re-used as the heal trigger.
    Returns {success: bool, agent_id: str}.
    """
    agent = kernel.registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    status = agent.status
    last_event_type = status.get("last_event_type")
    last_severity   = status.get("last_severity")

    if last_event_type is None:
        raise HTTPException(
            status_code=409,
            detail=f"Agent {agent_id} has not yet emitted an event — nothing to heal",
        )

    # Reconstruct a minimal WatchdogEvent for the heal call
    try:
        etype = EventType[last_event_type]
        esev  = EventSeverity[last_severity]
    except KeyError:
        raise HTTPException(status_code=500, detail="Cannot reconstruct event type")

    synthetic_event = WatchdogEvent(
        event_type = etype,
        severity   = esev,
        agent_id   = agent_id,
        source     = agent.source,
        message    = f"Manual heal triggered via dashboard at {time.time():.0f}",
    )

    try:
        success = await agent.heal(synthetic_event)
    except Exception as exc:
        logger.error("Manual heal raised for %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    kernel.strategy.record_result(agent_id, success)
    return {"success": success, "agent_id": agent_id}


@router.get("/mind")
async def get_mind_snapshot() -> dict:
    """
    Full SharedMind snapshot: all agent shards, state counts, consensus stats.

    This shows the collective intelligence view — what every agent knows
    about every other agent via the shared shard synchronization.
    """
    return shared_mind.global_snapshot()


@router.get("/mind/timeline")
async def get_mind_timeline(n: int = 100) -> list:
    """
    Return the last *n* entries from the global mind timeline.

    The timeline records every shard sync and broadcast message across
    all agents in chronological order.
    """
    n = min(n, 2000)
    return shared_mind.timeline(n)


@router.get("/mind/shards/{agent_id:path}")
async def get_mind_shard(agent_id: str) -> dict:
    """Return the shard for a specific agent (includes observation history)."""
    shard = shared_mind.get_shard(agent_id)
    if shard is None:
        raise HTTPException(status_code=404, detail=f"Shard not found: {agent_id}")
    return shard.to_dict()


@router.get("/mind/consensus")
async def get_consensus_stats() -> dict:
    """Return consensus engine statistics (rounds, approvals, rejections)."""
    snap = kernel.health_snapshot()
    mind = snap.get("mind", {})
    return {
        "consensus_stats": mind.get("consensus_stats", {}),
        "kernel_rejects":  snap.get("consensus_rejects", 0),
    }


@router.get("/summary")
async def get_summary() -> dict:
    """Compact summary: total counts and severity breakdown."""
    snap = kernel.health_snapshot()
    return {
        "started":         snap["started"],
        "total_agents":    snap["total_agents"],
        "critical_events": snap["critical_events"],
        "heals_performed": snap["heals_performed"],
        "severity_counts": snap["severity_counts"],
        "bus_stats":       snap["bus_stats"],
    }


# ── Standalone app (for running dashboard independently) ─────────────────────

app = FastAPI(title="Watchdog Dashboard", version="1.0.0")
app.include_router(router)


@app.on_event("startup")
async def _startup() -> None:
    event_bus.subscribe(_capture_event)
    await kernel.start()
    logger.info("Watchdog dashboard standalone mode started")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await kernel.stop()
    logger.info("Watchdog dashboard stopped")
