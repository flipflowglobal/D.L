"""
executor.py — Task dispatch layer.

Receives a structured payload and routes it to the appropriate
tool via the ToolRegistry.  Used by aureon_server.py and the
DL_SYSTEM orchestrator.

Payload schema:
    {
        "function_name": str,   # registered tool name
        "arguments":     dict,  # kwargs forwarded to the tool
    }
"""

from __future__ import annotations

import logging
from typing import Any

from tools import registry

logger = logging.getLogger("aureon.executor")


async def execute_task(payload: dict) -> Any:
    """
    Dispatch a task payload to its registered tool.

    Args:
        payload: dict with keys 'function_name' (str) and
                 'arguments' (dict).

    Returns:
        Whatever the tool returns.

    Raises:
        ValueError: unknown tool name or malformed payload.
        TypeError:  arguments dict has wrong types for the tool.
    """
    if not isinstance(payload, dict):
        raise TypeError(f"payload must be a dict, got {type(payload).__name__}")

    tool_name = payload.get("function_name")
    if not tool_name or not isinstance(tool_name, str):
        raise ValueError("payload must contain a non-empty 'function_name' string")

    args = payload.get("arguments", {})
    if not isinstance(args, dict):
        raise TypeError(
            f"'arguments' must be a dict, got {type(args).__name__}"
        )

    logger.debug("Executing tool '%s' with args: %s", tool_name, list(args.keys()))
    try:
        result = await registry.execute(tool_name, **args)
        logger.debug("Tool '%s' succeeded", tool_name)
        return result
    except ValueError:
        raise
    except Exception as exc:
        logger.error("Tool '%s' raised %s: %s", tool_name, type(exc).__name__, exc)
        raise
