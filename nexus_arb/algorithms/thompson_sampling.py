"""
nexus_arb.algorithms.thompson_sampling
=======================================

Thompson Sampling bandit for optimal DEX routing.

Usage in AUREON
---------------
  - DEX selection: given K DEXes (Uniswap V3, SushiSwap, Curve, Balancer, …),
    select the one most likely to offer the best execution price for the next trade.
  - Exploration vs exploitation: automatically balances trying under-sampled DEXes
    against exploiting known-good venues.
  - Regime adaptation: Beta posterior updates make the bandit responsive to
    market regime shifts (e.g. a DEX gaining/losing liquidity).

Theory
------
Thompson Sampling maintains a Beta(α_k, β_k) posterior over the success
probability of each arm k.  At each round:
  1. Sample θ_k ~ Beta(α_k, β_k) for every arm
  2. Select arm k* = argmax θ_k
  3. Observe reward r ∈ [0, 1]
  4. Update: if r > threshold → α_{k*} += 1, else β_{k*} += 1

For continuous rewards in (0, 1) we threshold at the running mean.

Bayesian Regret Bound
---------------------
Thompson Sampling achieves O(√(K·T·log T)) Bayesian regret vs O(K·log T / Δ)
for UCB1, making it strictly preferable under prior uncertainty.

Formal Specification
---------------------
  Preconditions:
    - arms: list of arm names, len >= 1
    - alpha0, beta0 >= 1 (Jeffreys prior: alpha=beta=0.5 also acceptable)

  Postconditions:
    - select() returns a valid arm name from the provided list
    - update(arm, reward) does not raise for arm in arms, reward in [0,1]
    - expected_reward(arm) returns α/(α+β) ∈ (0, 1)

  Invariants:
    - α_k >= alpha0, β_k >= beta0 for all k (monotone non-decreasing)
    - select() is stochastic: same state → different outputs (unless seeded)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass
class ArmStats:
    """Per-arm Beta distribution parameters and sample statistics."""
    alpha:      float = 1.0    # prior + successes
    beta:       float = 1.0    # prior + failures
    n_selected: int   = 0
    n_rewarded: int   = 0
    total_reward: float = 0.0

    @property
    def expected_reward(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def reward_rate(self) -> float:
        return self.total_reward / self.n_selected if self.n_selected else 0.0


class ThompsonSamplingBandit:
    """
    Beta-Bernoulli Thompson Sampling bandit for DEX selection.

    Parameters
    ----------
    arms        : list of DEX names (arm identifiers)
    alpha0      : Beta prior alpha (pseudo-successes before any observation)
    beta0       : Beta prior beta  (pseudo-failures before any observation)
    reward_threshold : continuous reward values above this are treated as
                       "successes" for the Beta update (default 0.5)
    seed        : random seed for reproducibility

    Example
    -------
    >>> bandit = ThompsonSamplingBandit(["uniswap_v3", "sushiswap", "curve"])
    >>> arm = bandit.select()
    >>> reward = 0.8   # normalized profit
    >>> bandit.update(arm, reward)
    >>> bandit.expected_rewards()
    {'uniswap_v3': 0.667, 'sushiswap': 0.5, 'curve': 0.5}
    """

    def __init__(
        self,
        arms:              Sequence[str],
        alpha0:            float = 1.0,
        beta0:             float = 1.0,
        reward_threshold:  float = 0.5,
        seed:              Optional[int] = None,
    ) -> None:
        if not arms:
            raise ValueError("At least one arm is required")
        if alpha0 <= 0 or beta0 <= 0:
            raise ValueError("alpha0 and beta0 must be positive")

        self.arms = list(arms)
        self._threshold = reward_threshold
        self._rng = random.Random(seed)

        # Per-arm state
        self._stats: Dict[str, ArmStats] = {
            arm: ArmStats(alpha=alpha0, beta=beta0)
            for arm in self.arms
        }

    # ── public interface ───────────────────────────────────────────────────────

    def select(self) -> str:
        """
        Sample one θ_k from each arm's Beta posterior and return the argmax.

        Returns
        -------
        Name of the selected arm (DEX).
        """
        samples = {
            arm: self._beta_sample(stats.alpha, stats.beta)
            for arm, stats in self._stats.items()
        }
        return max(samples, key=samples.__getitem__)

    def select_top_k(self, k: int) -> List[str]:
        """
        Return the top-k arms ranked by Beta sample draw.
        Useful for constructing a fallback execution waterfall.
        """
        k = min(k, len(self.arms))
        samples = {
            arm: self._beta_sample(stats.alpha, stats.beta)
            for arm, stats in self._stats.items()
        }
        return sorted(samples, key=samples.__getitem__, reverse=True)[:k]

    def update(self, arm: str, reward: float) -> None:
        """
        Update the Beta posterior for arm based on observed reward.

        Parameters
        ----------
        arm    : the arm that was pulled (must be in self.arms)
        reward : observed reward ∈ [0, 1] (e.g. normalized profit)
        """
        if arm not in self._stats:
            raise ValueError(f"Unknown arm: {arm!r}. Valid arms: {self.arms}")
        reward = max(0.0, min(1.0, float(reward)))

        stats = self._stats[arm]
        stats.n_selected += 1
        stats.total_reward += reward

        # Soft Bernoulli update: success probability = reward
        if reward >= self._threshold:
            stats.alpha += reward               # weighted success
            stats.n_rewarded += 1
        else:
            stats.beta += (1.0 - reward)        # weighted failure

    def reset(self, arm: Optional[str] = None, alpha0: float = 1.0, beta0: float = 1.0) -> None:
        """
        Reset posterior for one arm (or all arms if arm is None).
        Useful on regime change detection.
        """
        targets = [arm] if arm else self.arms
        for a in targets:
            self._stats[a] = ArmStats(alpha=alpha0, beta=beta0)

    # ── read-only statistics ──────────────────────────────────────────────────

    def expected_rewards(self) -> Dict[str, float]:
        """Return {arm: E[θ_arm]} = {arm: α/(α+β)}."""
        return {arm: round(s.expected_reward, 4) for arm, s in self._stats.items()}

    def best_arm(self) -> str:
        """Return the arm with the highest expected reward (greedy / exploit)."""
        return max(self._stats, key=lambda a: self._stats[a].expected_reward)

    def stats(self) -> Dict[str, dict]:
        """Return full per-arm statistics."""
        return {
            arm: {
                "alpha":        round(s.alpha, 4),
                "beta":         round(s.beta, 4),
                "expected":     round(s.expected_reward, 4),
                "n_selected":   s.n_selected,
                "n_rewarded":   s.n_rewarded,
                "reward_rate":  round(s.reward_rate, 4),
            }
            for arm, s in self._stats.items()
        }

    def confidence_interval(self, arm: str, confidence: float = 0.95) -> tuple:
        """
        Return approximate (lower, upper) credible interval for arm's reward.
        Uses Wilson score as a fast analytic approximation.
        """
        s = self._stats[arm]
        n = s.alpha + s.beta
        p = s.expected_reward
        z = 1.96 if confidence == 0.95 else 2.576   # 95% or 99%
        margin = z * math.sqrt(p * (1 - p) / max(n, 1))
        return (max(0.0, round(p - margin, 4)), min(1.0, round(p + margin, 4)))

    # ── internal ──────────────────────────────────────────────────────────────

    def _beta_sample(self, alpha: float, beta: float) -> float:
        """Sample from Beta(alpha, beta) using the standard library."""
        return self._rng.betavariate(alpha, beta)
