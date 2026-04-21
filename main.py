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
from typing import Any, Dict, Optional

# ── uvloop: 2-4x faster event loop (Linux/macOS only) ─────────────────────────
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except (ImportError, AttributeError):
    pass  # uvloop not available (Windows / Android) — falls back to asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from intelligence.memory import memory
from intelligence.autonomy import loop
from intelligence.trading_agent import (
    TradingAgentConfig,
    Strategy,
    Chain,
    Token,
    registry,
)

logger = logging.getLogger("aureon.main")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# ── Module-level task tracking ────────────────────────────────────────────────
_agent_task: asyncio.Task | None = None


# --------------------------------------------------
# REQUEST / RESPONSE MODELS
# --------------------------------------------------

class CreateAgentRequest(BaseModel):
    """Body for POST /agents — create a new multi-strategy trading agent."""
    name:            str     = Field("Agent",         description="Human-readable agent name")
    strategy:        Strategy = Field(Strategy.ARB,   description="Trading strategy")
    chain:           Chain    = Field(Chain.ETHEREUM,  description="Target blockchain")
    token:           Token    = Field(Token.ETH,       description="Primary token to trade")
    initial_capital: float   = Field(10_000.0,        description="Starting capital in USD")
    trade_size_eth:  float   = Field(0.05,            description="Max trade size (ETH per cycle)")
    min_profit_usd:  float   = Field(2.0,             description="Min estimated profit to trade")
    scan_interval:   int     = Field(30,              description="Seconds between cycles")
    dry_run:         bool    = Field(True,             description="Dry run — no real transactions")
    private_key:     Optional[str] = Field(None,      description="Hex private key; auto-generated if omitted")
    rpc_url:         Optional[str] = Field(None,      description="Override RPC URL")


# ── Lifespan: init / teardown ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup; ensure loop is stopped on shutdown."""
    await memory.init_db()
    logger.info("Memory database initialized")
    logger.info("Cognitive system online")
    logger.info("Multi-agent registry ready")
    yield
    # Graceful shutdown: signal the agent loop to stop and await it
    if loop.running:
        loop.running = False
        logger.info("Agent loop signalled to stop on shutdown")
    if _agent_task and not _agent_task.done():
        try:
            await asyncio.wait_for(_agent_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _agent_task.cancel()
            logger.warning("Agent task cancelled during shutdown")


# ── Application ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AUREON Cognitive System",
    description=(
        "Autonomous multi-strategy trading agent platform. "
        "Create agents with configurable strategy, chain, and token. "
        "Each agent auto-generates its own wallet and uses advanced "
        "algorithms (Bellman-Ford, PPO, CMA-ES, Thompson Sampling, UKF)."
    ),
    version="2.0",
    lifespan=lifespan,
)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/", summary="System identity")
async def root() -> dict:
    """Return system name and current run-state."""
    return {
        "system": "AUREON",
        "status": "running",
        "version": "2.0",
        "agents_active": registry.count(),
    }


@app.get("/health", summary="Health check")
async def health() -> dict:
    """Lightweight liveness probe — always 200 if the server is up."""
    return {"status": "ok"}


@app.get("/status", summary="Agent loop status")
async def status() -> dict:
    """Return whether the autonomous agent loop is currently active."""
    return {
        "agent_loop_running": loop.running,
        "multi_agent_count":  registry.count(),
        "strategies":         [s.value for s in Strategy],
        "chains":             [c.value for c in Chain],
        "tokens":             [t.value for t in Token],
    }


# --------------------------------------------------
# LEGACY AGENT START / STOP (backward compat)
# --------------------------------------------------

@app.post("/aureon/start", summary="Start the agent loop")
async def start_aureon_agent(agent_id: str) -> JSONResponse:
    """
    Launch the autonomous trading/reasoning loop for *agent_id*.

    Returns 409 if the loop is already running.
    """
    global _agent_task

    if not agent_id or not agent_id.strip():
        raise HTTPException(status_code=422, detail="agent_id must be a non-empty string")

    if loop.running:
        raise HTTPException(
            status_code=409,
            detail="Agent loop is already running. POST /aureon/stop first.",
        )

    loop.running = True
    _agent_task = asyncio.create_task(loop.run(agent_id))
    logger.info("Agent loop started for agent_id=%s", agent_id)

    return JSONResponse(
        status_code=200,
        content={"status": "agent started", "agent_id": agent_id},
    )


@app.post("/aureon/stop", summary="Stop the agent loop")
async def stop_aureon_agent() -> JSONResponse:
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


# --------------------------------------------------
# MULTI-AGENT API
# --------------------------------------------------

@app.post("/agents", status_code=201)
async def create_agent(req: CreateAgentRequest) -> Dict[str, Any]:
    """
    Create a new trading agent.

    - Automatically generates an Ethereum wallet unless `private_key` is supplied.
    - Returns wallet address, agent ID, and full config.
    - Agent is NOT started automatically — call POST /agents/{id}/start to run it.
    """
    config = TradingAgentConfig(
        name            = req.name,
        strategy        = req.strategy,
        chain           = req.chain,
        token           = req.token,
        initial_capital = req.initial_capital,
        trade_size_eth  = req.trade_size_eth,
        min_profit_usd  = req.min_profit_usd,
        scan_interval   = req.scan_interval,
        dry_run         = req.dry_run,
        private_key     = req.private_key,
        rpc_url         = req.rpc_url,
    )
    try:
        agent = registry.create(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    return {
        "agent_id":       agent.id,
        "name":           agent.config.name,
        "strategy":       agent.config.strategy.value,
        "chain":          agent.config.chain.value,
        "token":          agent.config.token.value,
        "wallet_address": agent.wallet["address"],
        "dry_run":        agent.config.dry_run,
        "status":         agent.status.value,
        "message":        "Agent created. Call POST /agents/{id}/start to begin trading.",
    }


@app.get("/agents")
async def list_agents() -> Dict[str, Any]:
    """List all registered agents with summary metrics."""
    return {
        "count":  registry.count(),
        "agents": registry.list_all(),
    }


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> Dict[str, Any]:
    """Get full details for a specific agent."""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return agent.to_dict()


@app.post("/agents/{agent_id}/start")
async def start_agent(agent_id: str) -> Dict[str, Any]:
    """Start a trading agent's autonomous loop."""
    try:
        agent = await registry.start(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "agent_id": agent.id,
        "status":   agent.status.value,
        "message":  "Agent started",
    }


@app.post("/agents/{agent_id}/stop")
async def stop_agent(agent_id: str) -> Dict[str, Any]:
    """Stop a running agent gracefully."""
    try:
        agent = await registry.stop(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "agent_id":    agent.id,
        "status":      agent.status.value,
        "cycle_count": agent.cycle_count,
        "message":     "Agent stopped",
    }


@app.get("/agents/{agent_id}/performance")
async def agent_performance(agent_id: str) -> Dict[str, Any]:
    """
    Get detailed performance metrics for an agent:
    PnL, ROI, drawdown, trade count, current position, and last cycle result.
    """
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return agent.performance()


@app.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    """Stop and remove an agent from the registry."""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    await agent.stop()
    registry.remove(agent_id)


# --------------------------------------------------
# WALLET GENERATION ENDPOINT
# --------------------------------------------------

@app.post("/wallet/generate")
async def generate_wallet_endpoint() -> Dict[str, str]:
    """
    Generate a fresh Ethereum wallet (address + private key).

    ⚠️  The private key is returned once — store it securely.
    """
    from intelligence.trading_agent import generate_wallet
    wallet = generate_wallet()
    return {
        "address":     wallet["address"],
        "private_key": wallet["private_key"],
        "warning":     "Store the private key securely. It is never stored server-side.",
    }


# --------------------------------------------------
# STRATEGIES / CHAINS / TOKENS DISCOVERY ENDPOINTS
# --------------------------------------------------

@app.get("/strategies")
async def list_strategies() -> Dict[str, Any]:
    """List all available trading strategies with descriptions."""
    return {
        "strategies": {
            "arb":            "Bellman-Ford multi-hop DEX arbitrage",
            "ppo":            "PPO reinforcement-learning actor-critic policy",
            "mean_reversion": "CMA-ES optimised mean-reversion signal",
            "flash_loan":     "Thompson Sampling DEX routing with Aave V3 flash loans",
            "adaptive":       "UKF Kalman price filter + Thompson Sampling bandit routing",
        }
    }


@app.get("/chains")
async def list_chains() -> Dict[str, Any]:
    """List all supported blockchains with chain IDs."""
    from intelligence.trading_agent import CHAIN_META
    return {"chains": CHAIN_META}


@app.get("/tokens")
async def list_tokens() -> Dict[str, Any]:
    """List all supported tokens."""
    return {
        "tokens": [t.value for t in Token],
        "descriptions": {
            "ETH":   "Ethereum (native)",
            "USDC":  "USD Coin (stablecoin)",
            "WBTC":  "Wrapped Bitcoin",
            "ARB":   "Arbitrum governance token",
            "MATIC": "Polygon native token",
        },
    }


# ── Memory endpoints ────────────────────────────────────────────────────────────

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
