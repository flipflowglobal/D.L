# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
Unscented Kalman Filter (UKF) for Price State Estimation.

State vector:  x = [price, velocity, acceleration, log_spread]
Observation:   z = [observed_price]

The UKF captures non-linear price dynamics via the Unscented Transform,
providing smoothed price estimates and 1-block ahead predictions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class UKFState:
    price: float
    velocity: float
    acceleration: float
    log_spread: float
    price_std: float
    velocity_std: float
    is_trending_up: bool
    prediction_1block: float


class PriceUKF:
    """4-dimensional Unscented Kalman Filter for price dynamics."""

    def __init__(self, config: dict, dt: float = 0.25) -> None:
        ukf_cfg = config.get("algorithms", {}).get("ukf", {})
        self.dt    = dt
        self.n     = 4
        self.m     = 1
        self.initialized = False

        alpha  = ukf_cfg.get("alpha", 1e-3)
        beta   = ukf_cfg.get("beta",  2.0)
        kappa  = ukf_cfg.get("kappa", 0.0)
        lam    = alpha ** 2 * (self.n + kappa) - self.n

        n = self.n
        self.Wm    = np.full(2 * n + 1, 1.0 / (2 * (n + lam)))
        self.Wm[0] = lam / (n + lam)
        self.Wc    = self.Wm.copy()
        self.Wc[0] += (1 - alpha ** 2 + beta)
        self.lambda_ = lam

        q = ukf_cfg.get("process_noise", 0.001)
        self.Q = np.diag([q, q * 10, q * 100, q * 0.1])

        r = ukf_cfg.get("measurement_noise", 0.01)
        self.R = np.array([[r]])

        self.x = np.zeros(self.n)
        self.P = np.eye(self.n) * 1000

    def _f(self, x: np.ndarray) -> np.ndarray:
        dt = self.dt
        p, v, a, ls = x
        return np.array([
            p + dt * v + 0.5 * dt ** 2 * a,
            v + dt * a,
            a * 0.95,
            ls * 0.99
        ])

    def _h(self, x: np.ndarray) -> np.ndarray:
        return np.array([x[0]])

    def _sigma_points(self, x: np.ndarray, P: np.ndarray) -> np.ndarray:
        n = self.n
        lam = self.lambda_
        try:
            L = np.linalg.cholesky((n + lam) * P)
        except np.linalg.LinAlgError:
            P += np.eye(n) * 1e-6
            L = np.linalg.cholesky((n + lam) * P)
        sigmas = np.zeros((2 * n + 1, n))
        sigmas[0] = x
        for i in range(n):
            sigmas[i + 1]     = x + L[:, i]
            sigmas[n + i + 1] = x - L[:, i]
        return sigmas

    def initialize(self, price: float, spread: float = 0.001) -> None:
        self.x = np.array([price, 0.0, 0.0, np.log(max(spread, 1e-8))])
        self.P = np.diag([price * 0.01, price * 0.001, price * 0.0001, 1.0])
        self.initialized = True

    def update(self, price_obs: float) -> UKFState:
        if not self.initialized:
            self.initialize(price_obs)
            return self._to_state()

        sigmas   = self._sigma_points(self.x, self.P)
        sigmas_f = np.array([self._f(s) for s in sigmas])
        x_pred   = np.dot(self.Wm, sigmas_f)
        P_pred   = self.Q.copy()
        for i, s in enumerate(sigmas_f):
            d = s - x_pred
            P_pred += self.Wc[i] * np.outer(d, d)

        sigmas_h = np.array([self._h(s) for s in sigmas_f])
        z_pred   = np.dot(self.Wm, sigmas_h)
        S        = self.R.copy()
        for i, s in enumerate(sigmas_h):
            d = s - z_pred
            S += self.Wc[i] * np.outer(d, d)

        Pxz = np.zeros((self.n, self.m))
        for i, (sf, sh) in enumerate(zip(sigmas_f, sigmas_h)):
            Pxz += self.Wc[i] * np.outer(sf - x_pred, sh - z_pred)

        K = Pxz @ np.linalg.inv(S)
        z = np.array([price_obs])
        self.x = x_pred + K @ (z - z_pred)
        self.P = P_pred - K @ S @ K.T
        self.P = 0.5 * (self.P + self.P.T)
        self.P += np.eye(self.n) * 1e-10
        return self._to_state()

    def _to_state(self) -> UKFState:
        price, vel, acc, log_spread = self.x
        P_diag = np.diag(self.P)
        dt     = self.dt
        pred   = price + dt * vel + 0.5 * dt ** 2 * acc
        return UKFState(
            price=float(price),
            velocity=float(vel),
            acceleration=float(acc),
            log_spread=float(log_spread),
            price_std=float(np.sqrt(max(P_diag[0], 0))),
            velocity_std=float(np.sqrt(max(P_diag[1], 0))),
            is_trending_up=float(vel) > 0,
            prediction_1block=float(pred)
        )

    def predict_n_blocks(self, n: int) -> float:
        x = self.x.copy()
        for _ in range(n):
            x = self._f(x)
        return float(x[0])


class MultiTokenUKF:
    """Manages one UKF per (token_in, token_out) pair."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self._filters: dict[tuple[str, str], PriceUKF] = {}

    def update(self, token_in: str, token_out: str, price: float) -> UKFState:
        key = (token_in, token_out)
        if key not in self._filters:
            self._filters[key] = PriceUKF(self.config)
        return self._filters[key].update(price)

    def is_price_moving_favorably(self, token_in: str, token_out: str) -> bool:
        key = (token_in, token_out)
        if key not in self._filters:
            return True
        return self._filters[key]._to_state().is_trending_up
