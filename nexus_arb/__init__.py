# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb — AUREON Mathematical Engine
=======================================

Advanced algorithmic layer for the AUREON DeFi trading system.

Packages:
  algorithms/  — Bellman-Ford arb finder, CMA-ES optimizer, UKF price filter,
                  Thompson Sampling DEX bandit, PPO trading policy
  math/        — Gas cost models, bonding curves, Markov chain analysis,
                  M/M/1 queueing theory for mempool design
  flash_loan_executor — Python executor for NexusFlashReceiver.sol

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

from .flash_loan_executor import (
    FlashLoanExecutor,
    SwapStep,
    DEX_UNISWAP_V3,
    DEX_CURVE,
    DEX_BALANCER,
    DEX_CAMELOT_V3,
)

__all__ = [
    "BellmanFordArb",
    "CMAES",
    "UnscentedKalmanFilter",
    "ThompsonSamplingBandit",
    "TradingPolicy",
    "FlashLoanExecutor",
    "SwapStep",
    "DEX_UNISWAP_V3",
    "DEX_CURVE",
    "DEX_BALANCER",
    "DEX_CAMELOT_V3",
]
