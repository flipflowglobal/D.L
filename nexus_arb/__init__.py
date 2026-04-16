"""
nexus_arb — AUREON Mathematical Engine
=======================================

Advanced algorithmic layer for the AUREON DeFi trading system.

Packages:
  algorithms/  — Bellman-Ford arb finder, CMA-ES optimizer, UKF price filter,
                  Thompson Sampling DEX bandit, PPO trading policy
  math/        — Gas cost models, bonding curves, Markov chain analysis,
                  M/M/1 queueing theory for mempool design

Design Invariants:
  - All modules are pure-Python (numpy optional) — safe to import without RPC
  - Offline-first: every class has a simulation / offline path
  - Zero external network calls at import time
  - Thread-safe stateless computations; stateful objects are not shared
"""

from nexus_arb.algorithms import (
    BellmanFordArb,
    CMAES,
    UnscentedKalmanFilter,
    ThompsonSamplingBandit,
    TradingPolicy,
)

__all__ = [
    "BellmanFordArb",
    "CMAES",
    "UnscentedKalmanFilter",
    "ThompsonSamplingBandit",
    "TradingPolicy",
]
