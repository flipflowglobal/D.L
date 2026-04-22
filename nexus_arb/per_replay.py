# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/per_replay.py — Prioritized Experience Replay (SumTree + PERBuffer).

Priority:  p_i = |TD_error_i|^alpha + eps    alpha=0.6, eps=1e-6
Sample:    P(i) = p_i / sum(p_j)
IS weight: w_i = (N * P(i))^{-beta} / max_j(w_j)
beta anneal: beta(t) = beta0 + (1-beta0)*min(t/T, 1)  beta0=0.4
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np


class SumTree:
    """Binary sum-tree for O(log N) prioritised sampling."""

    def __init__(self, capacity: int) -> None:
        cap = 1
        while cap < capacity:
            cap <<= 1
        self.capacity = cap
        self.tree = np.zeros(2 * cap, dtype=float)
        self.data: List[Any] = [None] * cap
        self._ptr = 0
        self._size = 0

    def _propagate(self, leaf_node: int, change: float) -> None:
        idx = leaf_node
        while idx > 1:
            idx //= 2
            self.tree[idx] += change

    def _retrieve(self, idx: int, target: float) -> int:
        while True:
            left = 2 * idx
            right = left + 1
            if left >= len(self.tree):
                return idx
            if target <= self.tree[left]:
                idx = left
            else:
                target -= self.tree[left]
                idx = right

    def add(self, priority: float, data: Any) -> None:
        leaf_node = self._ptr + self.capacity
        change = float(priority) - self.tree[leaf_node]
        self.tree[leaf_node] = float(priority)
        self._propagate(leaf_node, change)
        self.data[self._ptr] = data
        self._ptr = (self._ptr + 1) % self.capacity
        self._size = min(self._size + 1, self.capacity)

    def update(self, data_idx: int, priority: float) -> None:
        if not (0 <= data_idx < self.capacity):
            return
        leaf_node = data_idx + self.capacity
        change = float(priority) - self.tree[leaf_node]
        self.tree[leaf_node] = float(priority)
        self._propagate(leaf_node, change)

    def sample(self, n: int) -> List[Tuple[int, float, Any]]:
        results = []
        total = self.total()
        if total <= 0 or self._size == 0:
            return results
        segment = total / n
        for k in range(n):
            lo = segment * k
            hi = segment * (k + 1)
            target = float(np.random.uniform(lo, hi))
            node = self._retrieve(1, target)
            data_idx = node - self.capacity
            data_idx = max(0, min(data_idx, self._size - 1))
            results.append((data_idx, float(self.tree[node]), self.data[data_idx]))
        return results

    def total(self) -> float:
        return float(self.tree[1])

    def __len__(self) -> int:
        return self._size


class PERBuffer:
    """Prioritized Experience Replay buffer backed by SumTree."""

    def __init__(
        self,
        capacity: int = 10_000,
        alpha: float = 0.6,
        beta_start: float = 0.4,
        beta_end: float = 1.0,
        T_anneal: int = 100_000,
        eps: float = 1e-6,
    ) -> None:
        self._tree = SumTree(capacity)
        self._capacity = capacity
        self._alpha = alpha
        self._beta_s = beta_start
        self._beta_e = beta_end
        self._T = T_anneal
        self._eps = eps
        self._step = 0
        self._max_p = 1.0

    def _priority(self, td_error: float) -> float:
        return (abs(float(td_error)) + self._eps) ** self._alpha

    def _beta(self) -> float:
        frac = min(self._step / max(self._T, 1), 1.0)
        return self._beta_s + (self._beta_e - self._beta_s) * frac

    def push(self, transition: Dict, td_error: float = 1.0) -> None:
        p = self._priority(td_error)
        self._max_p = max(self._max_p, p)
        self._tree.add(p, transition)
        self._step += 1

    def sample(self, batch_size: int) -> Tuple[List[Dict], List[int], np.ndarray]:
        beta = self._beta()
        n = len(self._tree)
        if n == 0:
            return [], [], np.array([])
        batch_size = min(batch_size, n)
        raw = self._tree.sample(batch_size)
        total = self._tree.total()
        idxs, weights, transitions = [], [], []
        for idx, priority, data in raw:
            if data is None:
                continue
            prob = priority / max(total, 1e-12)
            w = (n * prob) ** (-beta)
            idxs.append(idx)
            weights.append(w)
            transitions.append(data)
        if not weights:
            return [], [], np.array([])
        w_arr = np.array(weights, dtype=float)
        w_arr /= w_arr.max()
        return transitions, idxs, w_arr

    def update_priorities(self, indices: List[int], td_errors) -> None:
        td_errors = np.asarray(td_errors, dtype=float)
        for k, idx in enumerate(indices):
            td = float(td_errors[k]) if k < len(td_errors) else 1.0
            p = self._priority(td)
            self._max_p = max(self._max_p, p)
            self._tree.update(idx, p)

    def __len__(self) -> int:
        return len(self._tree)

    def hit_rate(self) -> float:
        return len(self._tree) / self._capacity
