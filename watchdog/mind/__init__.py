"""
watchdog/mind/__init__.py — Shared agent mind sub-package.

The mind sub-package implements shard-based state synchronization and
multi-agent consensus so the watchdog legion operates as a collective
intelligence rather than a collection of isolated monitors.

Quick reference
---------------
    from watchdog.mind import shared_mind, SharedMind, SyncBridge

    # Kernel gives each agent its bridge at startup:
    bridge = shared_mind.connect("my-agent")

    # Agent pushes state after every check():
    await bridge.push("healthy", cycle=42, latency_ms=12.3)

    # Agent declares healing:
    await bridge.set_healing(True)

    # Kernel runs consensus before executing a heal:
    result = await shared_mind.propose_heal(event)
    if result.approved:
        await agent.heal(event)
"""

from watchdog.mind.shard     import AgentShard, Observation
from watchdog.mind.core      import MindCore, mind_core
from watchdog.mind.consensus import (
    ConsensusEngine,
    ConsensusResult,
    HealProposal,
    Vote,
    consensus_engine,
)
from watchdog.mind.sync      import SharedMind, SyncBridge, shared_mind

__all__ = [
    # Shard
    "AgentShard",
    "Observation",
    # Core
    "MindCore",
    "mind_core",
    # Consensus
    "ConsensusEngine",
    "ConsensusResult",
    "HealProposal",
    "Vote",
    "consensus_engine",
    # Sync / façade
    "SharedMind",
    "SyncBridge",
    "shared_mind",
]
