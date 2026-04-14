# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
Thompson Sampling — Bayesian Multi-Armed Bandit for DEX/Route Selection.

Each DEX arm has a Beta(alpha, beta) posterior.  Reward-weighted updates
ensure the bandit prefers reliable AND profitable DEXes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class ArmStats:
    alpha: float = 1.0
    beta:  float = 1.0
    n_pulls: int = 0
    n_wins:  int = 0
    total_reward: float = 0.0
    last_pulled: float = field(default_factory=time.time)

    @property
    def mean_reward(self) -> float:
        return self.total_reward / max(self.n_pulls, 1)

    def sample(self, rng: np.random.Generator) -> float:
        return float(rng.beta(self.alpha, self.beta))

    def update(self, reward: float, decay: float = 0.995) -> None:
        self.alpha *= decay
        self.beta  *= decay
        if reward > 0:
            self.alpha += reward
            self.n_wins += 1
        else:
            self.beta += 1.0
        self.n_pulls += 1
        self.total_reward += max(reward, 0)
        self.last_pulled = time.time()


class ThompsonBandit:
    """Multi-armed bandit with Thompson Sampling."""

    def __init__(
        self,
        arms: list[str],
        alpha_init: float = 1.0,
        beta_init:  float = 1.0,
        decay_rate: float = 0.995,
        seed: Optional[int] = None
    ) -> None:
        self.arms: dict[str, ArmStats] = {
            arm: ArmStats(alpha=alpha_init, beta=beta_init)
            for arm in arms
        }
        self.decay = decay_rate
        self.rng   = np.random.default_rng(seed)

    def select(self) -> str:
        samples = {arm: stats.sample(self.rng) for arm, stats in self.arms.items()}
        return max(samples, key=samples.__getitem__)

    def select_top_k(self, k: int) -> list[str]:
        samples = [(arm, stats.sample(self.rng)) for arm, stats in self.arms.items()]
        samples.sort(key=lambda x: x[1], reverse=True)
        return [arm for arm, _ in samples[:k]]

    def update(self, arm: str, reward: float) -> None:
        if arm not in self.arms:
            self.arms[arm] = ArmStats(alpha=1.0, beta=1.0)
        self.arms[arm].update(reward, decay=self.decay)

    def add_arm(self, arm: str) -> None:
        if arm not in self.arms:
            self.arms[arm] = ArmStats()

    def stats_table(self) -> list[dict]:
        rows = []
        for arm, s in self.arms.items():
            rows.append({
                "arm": arm,
                "alpha": round(s.alpha, 3),
                "beta": round(s.beta, 3),
                "mean_posterior": round(s.alpha / (s.alpha + s.beta), 4),
                "n_pulls": s.n_pulls,
                "n_wins": s.n_wins,
                "mean_reward": round(s.mean_reward, 6)
            })
        rows.sort(key=lambda r: r["mean_posterior"], reverse=True)
        return rows


class DexBandit:
    """Specialised Thompson Sampling bandit for DEX x token-pair routing."""

    def __init__(self, config: dict) -> None:
        ts_cfg = config.get("algorithms", {}).get("thompson", {})
        self.alpha_init = ts_cfg.get("alpha_init", 1.0)
        self.beta_init  = ts_cfg.get("beta_init",  1.0)
        self.decay      = ts_cfg.get("decay_rate", 0.995)
        self._bandits: dict[tuple[str, str], ThompsonBandit] = {}

    def _get_bandit(self, token_in: str, token_out: str) -> ThompsonBandit:
        key = (token_in, token_out)
        if key not in self._bandits:
            self._bandits[key] = ThompsonBandit(
                arms=["uniswap_v3", "curve", "balancer", "camelot_v3"],
                alpha_init=self.alpha_init,
                beta_init=self.beta_init,
                decay_rate=self.decay
            )
        return self._bandits[key]

    def select_dex(self, token_in: str, token_out: str) -> str:
        return self._get_bandit(token_in, token_out).select()

    def select_top_dexes(self, token_in: str, token_out: str, k: int = 2) -> list[str]:
        return self._get_bandit(token_in, token_out).select_top_k(k)

    def record_outcome(
        self,
        token_in: str,
        token_out: str,
        dex: str,
        profit_eth: float,
        max_expected_profit_eth: float = 1.0
    ) -> None:
        reward = max(0.0, profit_eth / max(max_expected_profit_eth, 1e-9))
        reward = min(reward, 1.0)
        self._get_bandit(token_in, token_out).update(dex, reward)

    def record_failure(self, token_in: str, token_out: str, dex: str) -> None:
        self._get_bandit(token_in, token_out).update(dex, -1.0)

    def get_rankings(self) -> dict[tuple[str, str], list[dict]]:
        return {k: b.stats_table() for k, b in self._bandits.items()}

# Compatibility alias used by nexus_arb/algorithms/__init__.py
ThompsonSamplingBandit = DexBandit
