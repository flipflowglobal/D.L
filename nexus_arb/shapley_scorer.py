# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/shapley_scorer.py — Shapley value attribution for strategy ensembles.
"""
from __future__ import annotations

import logging
import math
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("nexus_arb.shapley_scorer")


class ShapleyScorer:
    def __init__(self, n_players: int, n_samples: int = 256, seed: Optional[int] = None, event_bus=None) -> None:
        self._n = n_players
        self._T = n_samples
        self._rng = np.random.default_rng(seed)
        self._bus = event_bus
        self._cache: Dict[frozenset, float] = {}
        self._phi_history: List[np.ndarray] = []

    def shapley_values(self, value_fn: Callable[[List[int]], float], weights: Optional[np.ndarray] = None) -> np.ndarray:
        n = self._n
        phi = np.zeros(n)
        for _ in range(self._T):
            perm = self._rng.permutation(n).tolist()
            S: List[int] = []
            v_prev = self._cached_value(value_fn, S)
            for i in perm:
                S_new = S + [i]
                v_new = self._cached_value(value_fn, S_new)
                phi[i] += (v_new - v_prev)
                v_prev = v_new
                S = S_new
        phi /= self._T
        if weights is not None:
            w = np.asarray(weights, dtype=float)
            if len(w) == n:
                phi *= w / (w.sum() + 1e-12) * n
        self._phi_history.append(phi.copy())
        return phi

    def _cached_value(self, value_fn: Callable[[List[int]], float], S: List[int]) -> float:
        key = frozenset(S)
        if key not in self._cache:
            try:
                self._cache[key] = float(value_fn(list(key)))
            except Exception:
                self._cache[key] = 0.0
        return self._cache[key]

    def clear_cache(self) -> None:
        self._cache.clear()

    def factor_attribution(self, factors: np.ndarray, profit: float, baseline_profit: float = 0.0) -> Dict[str, float]:
        gain = profit - baseline_profit
        d = len(factors)
        if d == 0 or gain == 0.0:
            return {}
        def v(S: List[int]) -> float:
            if not S:
                return 0.0
            return gain * float(np.mean(factors[S]))
        phi = self.shapley_values(v)
        total = float(phi.sum())
        if abs(total) < 1e-12:
            return {str(i): float(phi[i]) for i in range(d)}
        phi_norm = phi * gain / total
        return {str(i): float(phi_norm[i]) for i in range(d)}

    def bandit_allocation(self, arm_values: Dict[str, float], total_budget: float = 1.0) -> Dict[str, float]:
        arms = list(arm_values.keys())
        vals = np.array([arm_values[a] for a in arms], dtype=float)
        n = len(arms)
        if n == 0:
            return {}
        def v(S: List[int]) -> float:
            if not S:
                return 0.0
            return float(np.sum(vals[S]))
        scorer = ShapleyScorer(n_players=n, n_samples=min(self._T, 128), seed=42)
        phi = scorer.shapley_values(v)
        phi = np.clip(phi, 0.0, None)
        phi_sum = phi.sum()
        fracs = np.ones(n) / n if phi_sum < 1e-12 else phi / phi_sum
        return {arms[i]: float(fracs[i] * total_budget) for i in range(n)}

    def regime_attribution(self, strategies: List[str], pnl_per_regime: Dict[str, Dict[str, float]], current_regime: str) -> Dict[str, float]:
        n = len(strategies)
        if n == 0:
            return {}
        regime_pnl = pnl_per_regime.get(current_regime, {})
        vals = np.array([regime_pnl.get(s, 0.0) for s in strategies], dtype=float)
        def v(S: List[int]) -> float:
            if not S:
                return 0.0
            return float(np.sum(vals[S]))
        scorer = ShapleyScorer(n_players=n, n_samples=min(self._T, 64), seed=0)
        phi = scorer.shapley_values(v)
        return {strategies[i]: float(phi[i]) for i in range(n)}

    def exact_shapley(self, value_fn: Callable[[List[int]], float]) -> np.ndarray:
        n = self._n
        if n > 16:
            logger.warning("exact_shapley called with n=%d > 16; falling back to MC", n)
            return self.shapley_values(value_fn)
        phi = np.zeros(n)
        fact = math.factorial
        for i in range(n):
            others = [j for j in range(n) if j != i]
            for mask in range(1 << (n - 1)):
                S = [others[k] for k in range(n - 1) if mask & (1 << k)]
                s = len(S)
                weight = fact(s) * fact(n - s - 1) / fact(n)
                v_with = self._cached_value(value_fn, S + [i])
                v_without = self._cached_value(value_fn, S)
                phi[i] += weight * (v_with - v_without)
        self._phi_history.append(phi.copy())
        return phi

    def running_mean_phi(self) -> Optional[np.ndarray]:
        if not self._phi_history:
            return None
        return np.mean(self._phi_history, axis=0)

    def phi_trend(self) -> Optional[np.ndarray]:
        if len(self._phi_history) < 2:
            return None
        arr = np.array(self._phi_history)
        return arr[-1] - arr[-2]

    @property
    def n_players(self) -> int:
        return self._n

    @property
    def cache_size(self) -> int:
        return len(self._cache)
