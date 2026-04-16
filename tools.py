"""
tools.py — AUREON Tool Registry.

Provides a decorator-based registry for async tools that can be
invoked by name from executor.py or any other caller.

Usage:
    from tools import registry

    @registry.register("my_tool")
    async def my_tool(arg1: str, arg2: int = 0) -> dict:
        ...

    result = await registry.execute("my_tool", arg1="hello")
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("aureon.tools")


class ToolRegistry:
    """Registry mapping string names to async callable tools."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def register(self, name: str) -> Callable:
        """
        Decorator: register an async function under *name*.

        Args:
            name: unique tool identifier (e.g. "ping", "get_price").

        Returns:
            The original function unchanged (decorator passes through).
        """
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string")

        def decorator(func: Callable) -> Callable:
            if name in self.tools:
                logger.warning("Tool '%s' is being overwritten", name)
            self.tools[name] = func
            logger.debug("Registered tool '%s'", name)
            return func

        return decorator

    async def execute(self, name: str, **kwargs: Any) -> Any:
        """
        Call the registered tool *name* with **kwargs.

        Args:
            name:   tool name (must be registered).
            kwargs: forwarded to the tool function.

        Returns:
            Whatever the tool returns.

        Raises:
            ValueError: tool name not in registry.
        """
        if name not in self.tools:
            available = ", ".join(sorted(self.tools)) or "<none>"
            raise ValueError(
                f"Tool '{name}' not found. Available tools: {available}"
            )
        logger.debug("Executing tool '%s'", name)
        return await self.tools[name](**kwargs)

    def list_tools(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(self.tools)

    def is_registered(self, name: str) -> bool:
        """Return True if *name* is a registered tool."""
        return name in self.tools


# ── Module-level singleton ────────────────────────────────────────────────────
registry = ToolRegistry()


# ── Built-in tools ────────────────────────────────────────────────────────────

@registry.register("ping")
async def tool_ping() -> dict:
    """Health-check tool — always returns pong."""
    return {"message": "pong from aureon"}


@registry.register("list_tools")
async def tool_list_tools() -> dict:
    """Return the list of all registered tool names."""
    return {"tools": registry.list_tools()}
