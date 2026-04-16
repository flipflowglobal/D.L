"""
watchdog/__init__.py — Watchdog Legion public API.

Quick-start
-----------
    import asyncio
    from watchdog import kernel, start_watchdog, stop_watchdog

    async def main():
        await start_watchdog()   # launches all agents
        ...
        await stop_watchdog()

Or integrate into an existing FastAPI app:

    from fastapi import FastAPI
    from watchdog.dashboard import router as watchdog_router
    from watchdog import kernel

    app = FastAPI()
    app.include_router(watchdog_router)

    @app.on_event("startup")
    async def startup():
        await kernel.start()

    @app.on_event("shutdown")
    async def shutdown():
        await kernel.stop()
"""

from __future__ import annotations

from watchdog.event_bus         import EventBus, EventSeverity, EventType, WatchdogEvent, event_bus
from watchdog.kernel            import WatchdogKernel, kernel
from watchdog.registry          import AgentRegistry
from watchdog.healing.actions   import HealingStrategy, healing_strategy
from watchdog.agents            import (
    WatchdogAgent,
    DatabaseAgent,
    FileAgent,
    ProcessAgent,
    ResourceAgent,
    ServiceAgent,
    TradeLoopAgent,
)


async def start_watchdog() -> None:
    """Start the module-level kernel singleton."""
    await kernel.start()


async def stop_watchdog() -> None:
    """Stop the module-level kernel singleton."""
    await kernel.stop()


def health_snapshot() -> dict:
    """Return the current watchdog health snapshot (JSON-safe dict)."""
    return kernel.health_snapshot()


__all__ = [
    # Core classes
    "WatchdogKernel",
    "AgentRegistry",
    "HealingStrategy",
    "EventBus",
    # Singletons
    "kernel",
    "event_bus",
    "healing_strategy",
    # Event types
    "EventType",
    "EventSeverity",
    "WatchdogEvent",
    # Agent classes
    "WatchdogAgent",
    "DatabaseAgent",
    "FileAgent",
    "ProcessAgent",
    "ResourceAgent",
    "ServiceAgent",
    "TradeLoopAgent",
    # Convenience functions
    "start_watchdog",
    "stop_watchdog",
    "health_snapshot",
]
