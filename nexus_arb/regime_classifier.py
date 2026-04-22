# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/regime_classifier.py — HMM market regime classifier.

4 regimes, 3D Gaussian emissions, Baum-Welch EM, Viterbi decode.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger("nexus_arb.regime_classifier")

REGIME_NAMES = ["BULL_TREND", "BEAR_TREND", "MEAN_REVERTING", "CRISIS"]
N_STATES = 4
OBS_DIM = 3
REESTIMATE_EVERY = 500


@dataclass
class RegimeResult:
    regime: str
    state_idx: int
    confidence: float
    regime_probs: List[float]
    duration_steps: int
    ts: float = field(default_factory=time.time)


class RegimeClassifier:
    """HMM market-regime classifier with Baum-Welch + Viterbi."""

    def __init__(self, event_bus=None) -> None:
        self._bus = event_bus
        self._pi = np.array([0.40, 0.30, 0.25, 0.05])
        self._A = np.array([
            [0.88, 0.07, 0.04, 0.01],
            [0.06, 0.87, 0.05, 0.02],
            [0.07, 0.07, 0.84, 0.02],
            [0.12, 0.12, 0.16, 0.60],
        ], dtype=float)
        self._mu = np.array([
            [0.25, 0.20, 0.15],
            [0.65, 0.60, 0.50],
            [0.45, 0.35, 0.30],
            [0.90, 0.85, 0.80],
        ], dtype=float)
        self._sig2 = np.array([
            [0.010, 0.010, 0.010],
            [0.015, 0.015, 0.015],
            [0.006, 0.007, 0.008],
            [0.020, 0.025, 0.025],
        ], dtype=float)
        self._alpha_prev: Optional[np.ndarray] = None
        self._obs_buf: List[np.ndarray] = []
        self._obs_count = 0
        self._cur_state: Optional[int] = None
        self._duration = 0
        self._last: Optional[RegimeResult] = None

    def _log_emit(self, obs: np.ndarray, j: int) -> float:
        diff = obs - self._mu[j]
        s2 = self._sig2[j]
        return -0.5 * float(np.sum(diff ** 2 / s2 + np.log(2 * math.pi * s2)))

    def _emit_all(self, obs: np.ndarray) -> np.ndarray:
        return np.array([math.exp(max(self._log_emit(obs, j), -700)) for j in range(N_STATES)])

    def _fwd_step(self, obs: np.ndarray, prev: Optional[np.ndarray]) -> np.ndarray:
        b = self._emit_all(obs)
        a = (prev @ self._A) * b if prev is not None else self._pi * b
        t = float(a.sum())
        return a / t if t > 1e-300 else np.ones(N_STATES) / N_STATES

    def _forward(self, seq: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        T = len(seq)
        alpha = np.zeros((T, N_STATES))
        scales = np.zeros(T)
        b = self._emit_all(seq[0])
        a = self._pi * b
        c = max(float(a.sum()), 1e-300)
        alpha[0] = a / c
        scales[0] = c
        for t in range(1, T):
            b = self._emit_all(seq[t])
            a = (alpha[t - 1] @ self._A) * b
            c = max(float(a.sum()), 1e-300)
            alpha[t] = a / c
            scales[t] = c
        return alpha, scales

    def _backward(self, seq: np.ndarray, scales: np.ndarray) -> np.ndarray:
        T = len(seq)
        beta = np.zeros((T, N_STATES))
        beta[T - 1] = 1.0
        for t in range(T - 2, -1, -1):
            b = self._emit_all(seq[t + 1])
            beta[t] = (self._A * b[None, :]) @ beta[t + 1]
            c = float(scales[t + 1])
            if c > 1e-300:
                beta[t] /= c
            beta[t] = np.clip(beta[t], 0, 1e300)
        return beta

    def _baum_welch(self) -> None:
        seq = np.array(self._obs_buf[-REESTIMATE_EVERY:], dtype=float)
        try:
            alpha, scales = self._forward(seq)
            beta = self._backward(seq, scales)
            gamma = alpha * beta
            gs = gamma.sum(axis=1, keepdims=True)
            gamma /= np.where(gs < 1e-300, 1e-300, gs)
            T = len(seq)
            xi = np.zeros((T - 1, N_STATES, N_STATES))
            for t in range(T - 1):
                b = self._emit_all(seq[t + 1])
                numer = alpha[t, :, None] * self._A * b[None, :] * beta[t + 1, None, :]
                d = max(float(numer.sum()), 1e-300)
                xi[t] = numer / d
            self._pi = np.clip(gamma[0] + 1e-10, 0, None)
            self._pi /= self._pi.sum()
            xi_sum = xi.sum(axis=0)
            g_sum = gamma[:-1].sum(axis=0)
            for i in range(N_STATES):
                row = xi_sum[i]
                d = max(float(g_sum[i]), 1e-300)
                self._A[i] = np.clip(row / d, 1e-10, 1.0)
                self._A[i] /= self._A[i].sum()
            for j in range(N_STATES):
                gj = gamma[:, j]
                d = max(float(gj.sum()), 1e-300)
                mu_j = (gj[:, None] * seq).sum(axis=0) / d
                var_j = (gj[:, None] * (seq - mu_j) ** 2).sum(axis=0) / d
                self._mu[j] = mu_j
                self._sig2[j] = np.clip(var_j, 1e-6, 10.0)
        except Exception as exc:
            logger.warning("Baum-Welch failed: %s", exc)

    def viterbi_decode(self, obs_sequence: List) -> Tuple[List[str], float]:
        seq = np.asarray(obs_sequence, dtype=float)
        if seq.ndim == 1:
            seq = np.tile(seq.reshape(-1, 1), (1, OBS_DIM))
        T = len(seq)
        log_A = np.log(np.clip(self._A, 1e-300, 1.0))
        log_pi = np.log(np.clip(self._pi, 1e-300, 1.0))
        ldelta = np.zeros((T, N_STATES))
        psi = np.zeros((T, N_STATES), dtype=int)
        for j in range(N_STATES):
            ldelta[0, j] = log_pi[j] + self._log_emit(seq[0], j)
        for t in range(1, T):
            for j in range(N_STATES):
                vals = ldelta[t - 1] + log_A[:, j]
                psi[t, j] = int(np.argmax(vals))
                ldelta[t, j] = float(np.max(vals)) + self._log_emit(seq[t], j)
        states = [0] * T
        states[T - 1] = int(np.argmax(ldelta[T - 1]))
        for t in range(T - 2, -1, -1):
            states[t] = psi[t + 1, states[t + 1]]
        return [REGIME_NAMES[s] for s in states], float(np.max(ldelta[T - 1]))

    def classify(self, obs_3d) -> Tuple[str, float]:
        r = self.classify_full(obs_3d)
        return r.regime, r.confidence

    def classify_full(self, obs_3d) -> RegimeResult:
        obs = np.clip(np.asarray(obs_3d, dtype=float).flatten()[:OBS_DIM], 0.0, 1.0)
        if len(obs) < OBS_DIM:
            obs = np.pad(obs, (0, OBS_DIM - len(obs)), constant_values=0.5)
        alpha = self._fwd_step(obs, self._alpha_prev)
        self._alpha_prev = alpha
        dom = int(np.argmax(alpha))
        conf = float(alpha[dom])
        name = REGIME_NAMES[dom]
        if self._cur_state == dom:
            self._duration += 1
        else:
            self._cur_state = dom
            self._duration = 1
        self._obs_buf.append(obs.copy())
        self._obs_count += 1
        if self._obs_count % REESTIMATE_EVERY == 0 and len(self._obs_buf) >= 20:
            self._baum_welch()
        result = RegimeResult(regime=name, state_idx=dom, confidence=conf,
                              regime_probs=alpha.tolist(), duration_steps=self._duration)
        self._last = result
        return result

    def reset(self) -> None:
        self._alpha_prev = None
        self._cur_state = None
        self._duration = 0
        self._last = None

    @property
    def current_regime(self) -> Optional[str]:
        return REGIME_NAMES[self._cur_state] if self._cur_state is not None else None

    @property
    def last_result(self) -> Optional[RegimeResult]:
        return self._last
