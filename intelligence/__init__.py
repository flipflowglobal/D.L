"""
intelligence — Autonomous agent brain.

Exports:
    memory  — persistent key-value store (SQLite)
    loop    — AgentLoop singleton (start/stop the trading cycle)
"""

from intelligence.memory import memory
from intelligence.autonomy import loop

__all__ = ["memory", "loop"]
