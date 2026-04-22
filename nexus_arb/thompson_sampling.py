# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/thompson_sampling.py — Hierarchical Thompson Sampler for DEX routing.

Arm posterior: Beta(alpha_k, beta_k)
Sample: theta_k ~ Beta(alpha_k, beta_k), select k* = argmax theta_k
Update: alpha_{k*} += r,  beta_{k*} += (1-r)
Time-decay: alpha_k *= gamma, beta_k *= gamma every epoch (gamma=0.995)
"""
from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger("nexus_arb.thompson_sampling")

DECAY_GAMMA = 0.995
MIN_ALPHA_BETA = 0.5


@dataclass
class ArmStats:
    alpha: float = 1.0
    beta: float = 1.0
    pulls: int = 0
    wins: int = 0
    last_reward: float = 0.0
    last_pull_ts: float = field(default_factory=time.time)

    @property
    def expected_reward(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def uncertainty(self) -> float:
        a, b = self.alpha, self.beta
        n = a + b
        return (a * b) / (n ** 2 * (n + 1))


class HierarchicalThompsonSampler:
    """Two-level hierarchical Thompson Sampler for DEX + chain selection."""

    CHAINS = ["arbitrum", "ethereum", "polygon", "base"]
    DEXES = ["uniswap_v3_100", "uniswap_v3_500", "uniswap_v3_3000",
              "sushiswap", "curve", "balancer", "camelot"]

    def __init__(self, arms: Optional[List[str]] = None, epsilon: float = 0.05,
                 decay_gamma: float = DECAY_GAMMA, event_bus=None) -> None:
        self._bus = event_bus
        self._epsilon = epsilon
        self._gamma = decay_gamma
        self._epoch = 0
        self._total_pulls = 0
        arm_names = arms if arms is not None else self.DEXES
        self._arms: Dict[str, ArmStats] = {a: ArmStats() for a in arm_names}
        self._meta: Dict[str, ArmStats] = {c: ArmStats() for c in self.CHAINS}
        self._reward_mean = 0.0
        self._reward_var = 1.0
        self._ewm_alpha = 0.05
        logger.info("HierarchicalThompsonSampler: %d arms, %d chains", len(self._arms), len(self._meta))

    def _normalize_reward(self, r: float) -> float:
        self._reward_mean = (1 - self._ewm_alpha) * self._reward_mean + self._ewm_alpha * r
        var_update = (r - self._reward_mean) ** 2
        self._reward_var = (1 - self._ewm_alpha) * self._reward_var + self._ewm_alpha * var_update
        std = math.sqrt(max(self._reward_var, 1e-9))
        z = (r - self._reward_mean) / std
        return float(np.clip((z + 3.0) / 6.0, 0.0, 1.0))

    def decay(self) -> None:
        self._epoch += 1
        for stats in self._arms.values():
            stats.alpha = max(stats.alpha * self._gamma, MIN_ALPHA_BETA)
            stats.beta = max(stats.beta * self._gamma, MIN_ALPHA_BETA)
        for stats in self._meta.values():
            stats.alpha = max(stats.alpha * self._gamma, MIN_ALPHA_BETA)
            stats.beta = max(stats.beta * self._gamma, MIN_ALPHA_BETA)

    def select(self, chain: Optional[str] = None) -> str:
        if not self._arms:
            raise ValueError("No arms registered")
        if random.random() < self._epsilon:
            arm = random.choice(list(self._arms.keys()))
            self._total_pulls += 1
            return arm
        samples = {
            arm: float(np.random.beta(max(stats.alpha, 0.01), max(stats.beta, 0.01)))
            for arm, stats in self._arms.items()
        }
        best = max(samples, key=lambda a: samples[a])
        self._total_pulls += 1
        return best

    def update(self, arm: str, reward: float) -> None:
        if arm not in self._arms:
            logger.warning("Unknown arm: %s", arm)
            return
        r_norm = self._normalize_reward(float(reward))
        stats = self._arms[arm]
        stats.alpha += r_norm
        stats.beta += (1.0 - r_norm)
        stats.pulls += 1
        stats.wins += int(r_norm > 0.5)
        stats.last_reward = float(reward)
        stats.last_pull_ts = time.time()

    def select_chain(self) -> str:
        if not self._meta:
            return self.CHAINS[0]
        samples = {
            c: float(np.random.beta(max(s.alpha, 0.01), max(s.beta, 0.01)))
            for c, s in self._meta.items()
        }
        return max(samples, key=lambda c: samples[c])

    def update_chain(self, chain: str, reward: float) -> None:
        if chain not in self._meta:
            self._meta[chain] = ArmStats()
        r_norm = self._normalize_reward(float(reward))
        meta = self._meta[chain]
        meta.alpha += r_norm
        meta.beta += (1.0 - r_norm)
        meta.pulls += 1

    def warm_start(self, historical: Dict[str, Dict]) -> None:
        for arm, rec in historical.items():
            if arm not in self._arms:
                self._arms[arm] = ArmStats()
            wins = int(rec.get("wins", 0))
            total = int(rec.get("total", 0))
            if total > 0:
                self._arms[arm].alpha = float(wins + 1)
                self._arms[arm].beta = float(total - wins + 1)
                self._arms[arm].pulls = total
                self._arms[arm].wins = wins

    def expected_reward(self, arm: str) -> float:
        if arm not in self._arms:
            return 0.5
        return self._arms[arm].expected_reward

    def best_arm(self) -> str:
        return max(self._arms, key=lambda a: self._arms[a].expected_reward)

    def snapshot(self) -> Dict:
        return {
            "epoch": self._epoch,
            "total_pulls": self._total_pulls,
            "arms": {
                arm: {
                    "alpha": s.alpha,
                    "beta": s.beta,
                    "expected_reward": s.expected_reward,
                    "uncertainty": s.uncertainty,
                    "pulls": s.pulls,
                    "wins": s.wins,
                }
                for arm, s in self._arms.items()
            },
            "chains": {
                c: {"expected_reward": s.expected_reward, "pulls": s.pulls}
                for c, s in self._meta.items()
            },
        }

    def add_arm(self, arm: str, alpha: float = 1.0, beta: float = 1.0) -> None:
        if arm not in self._arms:
            self._arms[arm] = ArmStats(alpha=alpha, beta=beta)

    def remove_arm(self, arm: str) -> bool:
        return bool(self._arms.pop(arm, None))
