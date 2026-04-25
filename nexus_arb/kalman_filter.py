# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/kalman_filter.py — IMM filter with 3 Square-Root UKFs.

Backward-compat class name: KalmanFilter.

State: x = [price, velocity]  n=2
Markov: pi = [[0.90,0.08,0.02],[0.05,0.93,0.02],[0.10,0.10,0.80]]
Mode 0 Trending:  x' = [p+v*dt, v*0.99]
Mode 1 Mean-rev:  x' = [p+theta*(0-p)*dt, 0]  theta=0.30
Mode 2 Jump-diff: x' = [p+J, 0]  J~N(0,0.05)
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

logger = logging.getLogger("nexus_arb.kalman_filter")

_REGIME_NAMES = ["trending", "mean_reverting", "jump_diffusion"]


@dataclass
class IMMState:
    price_est: float
    velocity_est: float
    price_variance: float
    regime_probs: List[float]
    dominant_regime: str
    dominant_confidence: float
    innovation: float
    filter_healthy: bool
    ts: float = field(default_factory=time.time)


class KalmanFilter:
    """IMM filter with 3 SR-UKFs. Drop-in replacement for old KalmanFilter."""

    _PI = np.array([
        [0.90, 0.08, 0.02],
        [0.05, 0.93, 0.02],
        [0.10, 0.10, 0.80],
    ], dtype=float)

    def __init__(self, event_bus=None) -> None:
        self._bus = event_bus
        self.n = 2
        self._n_modes = 3
        alpha, beta, kappa = 1e-3, 2.0, 0.0
        lam = alpha ** 2 * (self.n + kappa) - self.n
        self._lam = lam
        n_sig = 2 * self.n + 1
        self._Wm = np.full(n_sig, 1.0 / (2.0 * (self.n + lam)))
        self._Wc = np.full(n_sig, 1.0 / (2.0 * (self.n + lam)))
        self._Wm[0] = lam / (self.n + lam)
        self._Wc[0] = lam / (self.n + lam) + (1.0 - alpha ** 2 + beta)
        self._mu = np.array([1.0 / 3.0] * 3)
        self._x = [np.zeros(self.n) for _ in range(self._n_modes)]
        self._S = [np.eye(self.n) * 0.1 for _ in range(self._n_modes)]
        self._R = np.array([[0.001]])
        self._initialized = False
        self._last_state: Optional[IMMState] = None
        self._prev_regime: Optional[str] = None

    def _f(self, x: np.ndarray, mode: int, dt: float = 1.0) -> np.ndarray:
        p, v = float(x[0]), float(x[1])
        if mode == 0:
            return np.array([p + v * dt, v * 0.99])
        if mode == 1:
            return np.array([p + 0.30 * (0.0 - p) * dt, 0.0])
        J = float(np.random.normal(0.0, 0.05))
        return np.array([p + J, 0.0])

    def _sigma_pts(self, x: np.ndarray, S: np.ndarray) -> np.ndarray:
        n = self.n
        factor = math.sqrt(n + self._lam)
        pts = np.zeros((2 * n + 1, n))
        pts[0] = x
        for i in range(n):
            c = S[:, i] if S.shape[1] > i else np.zeros(n)
            pts[i + 1] = x + factor * c
            pts[n + i + 1] = x - factor * c
        return pts

    def _cholupdate(self, S: np.ndarray, v: np.ndarray, sign: float) -> np.ndarray:
        try:
            P = S @ S.T + sign * np.outer(v, v)
            P = 0.5 * (P + P.T) + 1e-8 * np.eye(P.shape[0])
            return np.linalg.cholesky(P)
        except np.linalg.LinAlgError:
            try:
                P = S @ S.T + 1e-6 * np.eye(self.n)
                return np.linalg.cholesky(P)
            except np.linalg.LinAlgError:
                return np.eye(self.n) * 0.1

    def _ukf_step(self, x: np.ndarray, S: np.ndarray, z: float, mode: int) -> tuple:
        try:
            Q_d = [0.01, 0.001] if mode == 0 else [0.05, 0.0001] if mode == 1 else [0.1, 0.0]
            sqrtQ = np.diag([math.sqrt(q) for q in Q_d])
            sigma = self._sigma_pts(x, S)
            sp = np.array([self._f(s, mode) for s in sigma])
            x_p = sp.T @ self._Wm
            dev = sp[1:] - x_p
            wc1 = np.sqrt(np.abs(self._Wc[1:]))
            A = np.vstack([dev * wc1[:, None], sqrtQ.T])
            try:
                _, R_qr = np.linalg.qr(A)
                S_p = R_qr[:self.n, :self.n].T
                S_p = S_p * np.where(np.diag(S_p) >= 0, 1.0, -1.0)[:, None]
            except np.linalg.LinAlgError:
                S_p = np.eye(self.n) * 0.1
            S_p = self._cholupdate(S_p, sp[0] - x_p, math.copysign(1.0, self._Wc[0]))
            z_s = sp[:, 0]
            z_p = float(self._Wm @ z_s)
            nu = z - z_p
            alpha_R = 0.05
            self._R = (1.0 - alpha_R) * self._R + alpha_R * np.array([[nu ** 2]])
            R_sc = float(self._R[0, 0])
            sqrtR = math.sqrt(max(R_sc, 1e-9))
            S_yy = math.sqrt(sum(self._Wc[i] * (z_s[i] - z_p) ** 2 for i in range(len(z_s))) + R_sc)
            S_yy = max(S_yy, sqrtR)
            Pxz = sum(self._Wc[i] * (sp[i] - x_p) * (z_s[i] - z_p) for i in range(len(sp)))
            K = Pxz / max(S_yy ** 2, 1e-12)
            x_n = x_p + K * nu
            S_n = self._cholupdate(S_p, K * S_yy, -1.0)
            var = max(S_yy ** 2, 1e-12)
            lik = math.exp(-0.5 * nu ** 2 / var) / (math.sqrt(2 * math.pi * var))
            if not np.all(np.isfinite(x_n)):
                return x, S, 1e-300
            return x_n, S_n, max(lik, 1e-300)
        except Exception:
            return x, S, 1e-300

    async def update(self, z: float) -> IMMState:
        async def _emit(name: str, data: dict) -> None:
            if self._bus is not None:
                try:
                    await self._bus.emit(name, {**data, "component": "kalman_filter"})
                except Exception:
                    pass

        z = float(z)
        if not math.isfinite(z) or z <= 0:
            await _emit("filter_anomaly", {"field": "observation", "value": z})
            if self._last_state is not None:
                return self._last_state
            z = 1.0

        if not self._initialized:
            for j in range(self._n_modes):
                self._x[j] = np.array([z, 0.0])
                self._S[j] = np.eye(self.n) * 0.1
            self._initialized = True

        c_bar = self._PI.T @ self._mu
        c_bar = np.clip(c_bar, 1e-10, None)
        mu_ij = (self._PI.T * self._mu[None, :]) / c_bar[:, None]
        x_mix = [sum(float(mu_ij[j, i]) * self._x[i] for i in range(self._n_modes)) for j in range(self._n_modes)]
        S_mix = []
        for j in range(self._n_modes):
            P = np.zeros((self.n, self.n))
            for i in range(self._n_modes):
                diff = self._x[i] - x_mix[j]
                P += float(mu_ij[j, i]) * (self._S[i] @ self._S[i].T + np.outer(diff, diff))
            P = 0.5 * (P + P.T) + 1e-8 * np.eye(self.n)
            try:
                S_mix.append(np.linalg.cholesky(P))
            except np.linalg.LinAlgError:
                S_mix.append(np.eye(self.n) * 0.1)

        lhoods = np.zeros(self._n_modes)
        x_new = [None] * self._n_modes
        S_new = [None] * self._n_modes
        for j in range(self._n_modes):
            xj, Sj, lh = self._ukf_step(np.asarray(x_mix[j]), S_mix[j], z, j)
            x_new[j], S_new[j], lhoods[j] = xj, Sj, lh

        unnorm = lhoods * c_bar
        total = float(unnorm.sum())
        mu_new = unnorm / total if total > 1e-300 else self._mu.copy()
        mu_new = np.clip(mu_new, 1e-4, 1.0)
        mu_new /= mu_new.sum()
        self._mu = mu_new
        for j in range(self._n_modes):
            self._x[j] = x_new[j]
            self._S[j] = S_new[j]

        x_fus = np.zeros(self.n)
        for j in range(self._n_modes):
            x_fus += float(self._mu[j]) * self._x[j]
        P_fus = np.zeros((self.n, self.n))
        for j in range(self._n_modes):
            diff = self._x[j] - x_fus
            P_fus += float(self._mu[j]) * (self._S[j] @ self._S[j].T + np.outer(diff, diff))

        dom_idx = int(np.argmax(self._mu))
        dom_regime = _REGIME_NAMES[dom_idx]
        price_var = float(max(P_fus[0, 0], 0.0))
        innovation = z - float(x_fus[0])
        healthy = np.all(np.isfinite(x_fus)) and price_var < 1e6

        if not healthy:
            await _emit("imm_divergence", {"reason": "NaN/Inf in fused estimate"})
            self.reset()

        state = IMMState(
            price_est=float(x_fus[0]),
            velocity_est=float(x_fus[1]),
            price_variance=price_var,
            regime_probs=self._mu.tolist(),
            dominant_regime=dom_regime,
            dominant_confidence=float(self._mu[dom_idx]),
            innovation=float(innovation),
            filter_healthy=healthy,
        )
        await _emit("imm_update", {"price_est": state.price_est, "regime": dom_regime})
        if self._prev_regime and self._prev_regime != dom_regime:
            await _emit("regime_change", {"from": self._prev_regime, "to": dom_regime})
        self._prev_regime = dom_regime
        self._last_state = state
        return state

    @property
    def price_estimate(self) -> float:
        return self._last_state.price_est if self._last_state else 0.0

    def predict(self, steps: int = 1) -> float:
        if self._last_state is None:
            return 0.0
        return self._last_state.price_est + self._last_state.velocity_est * steps

    def reset(self) -> None:
        self._mu = np.array([1.0 / 3.0] * 3)
        self._x = [np.zeros(self.n) for _ in range(self._n_modes)]
        self._S = [np.eye(self.n) * 0.1 for _ in range(self._n_modes)]
        self._R = np.array([[0.001]])
        self._initialized = False
        self._last_state = None
        self._prev_regime = None
        logger.info("KalmanFilter (IMM) reset")
