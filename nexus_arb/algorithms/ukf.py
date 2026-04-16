"""
nexus_arb.algorithms.ukf
=========================

Unscented Kalman Filter (UKF) for price state estimation.

Usage in AUREON
---------------
  - Price prediction: track latent [price, velocity] state from noisy DEX quotes
  - Anomaly detection: flag observations that deviate from predicted state by
    more than k·σ (Mahalanobis distance)
  - Regime change detection: monitor innovation covariance for structural breaks

Theory
------
The UKF propagates a set of carefully chosen "sigma points" through the
nonlinear state transition and observation functions, recovering the posterior
mean and covariance without linearization (unlike the Extended KF).

For a 2-state system [price, velocity]:
  - State transition: price_{t+1} = price_t + velocity_t · Δt  (constant velocity)
                      velocity_{t+1} = velocity_t  (random walk on velocity)
  - Observation:      z_t = price_t + noise

Complexity per update: O(n³) where n = state dimension (2 here, so O(1)).

Formal Specification
---------------------
  Preconditions:
    - observation z: float > 0 (positive price)
    - R_obs: float > 0   (observation noise variance)
    - Q_proc: np.ndarray shape (n,n) positive semi-definite (process noise)

  Postconditions:
    - state_mean: shape (n,) posterior mean [price, velocity]
    - state_cov:  shape (n,n) symmetric positive definite posterior covariance
    - innovation: float (z - predicted_z)
    - is_anomaly: bool (Mahalanobis distance > anomaly_threshold)

  Invariants:
    - state_cov remains positive definite (enforced via regularization)
    - price component of state_mean is always positive after first update
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import math
import numpy as np

import math


@dataclass
class UKFState:
    """Snapshot of UKF state after one update step."""
    mean:           np.ndarray    # shape (n_state,)
    covariance:     np.ndarray    # shape (n_state, n_state)
    innovation:     float         # z - z_predicted
    innovation_cov: float         # S (scalar for 1-D observation)
    gain:           np.ndarray    # Kalman gain, shape (n_state,)
    is_anomaly:     bool          # Mahalanobis distance > threshold


class UnscentedKalmanFilter:
    """
    Unscented Kalman Filter for tracking ETH/USD price dynamics.

    State vector: x = [price, velocity]  (velocity in USD/cycle)
    Observation:  z = noisy price quote from a DEX

    Parameters
    ----------
    dt              : time step between observations (seconds; default 30)
    process_noise   : (q_price, q_vel) standard deviations for process noise
    obs_noise_std   : observation noise standard deviation (USD)
    anomaly_thresh  : Mahalanobis distance that triggers anomaly flag (default 3σ)
    alpha, beta, kappa : UKF scaling parameters
    """

    def __init__(
        self,
        dt:             float = 30.0,
        process_noise:  tuple = (0.5, 0.01),
        obs_noise_std:  float = 5.0,
        anomaly_thresh: float = 3.0,
        alpha:          float = 1e-3,
        beta:           float = 2.0,
        kappa:          float = 0.0,
    ) -> None:
        self.n  = 2          # state dimension: [price, velocity]
        self.m  = 1          # observation dimension
        self.dt = dt

        # Process noise covariance Q
        q_p, q_v = process_noise
        self.Q = np.diag([q_p ** 2, q_v ** 2])

        # Observation noise variance R
        self.R = obs_noise_std ** 2

        self.anomaly_thresh = anomaly_thresh

        # UKF sigma-point parameters
        self._alpha = alpha
        self._beta  = beta
        self._kappa = kappa
        lam = alpha ** 2 * (self.n + kappa) - self.n
        self._lam = lam

        # Weights for sigma points (mean and covariance)
        n_sigma = 2 * self.n + 1
        self._Wm = np.full(n_sigma, 1 / (2 * (self.n + lam)))
        self._Wc = np.full(n_sigma, 1 / (2 * (self.n + lam)))
        self._Wm[0] = lam / (self.n + lam)
        self._Wc[0] = lam / (self.n + lam) + (1 - alpha ** 2 + beta)

        # State — initialized on first observation
        self._x: Optional[np.ndarray] = None    # mean
        self._P: Optional[np.ndarray] = None    # covariance

    # ── sigma points ──────────────────────────────────────────────────────────

    def _sigma_points(self, x: np.ndarray, P: np.ndarray) -> np.ndarray:
        """
        Generate 2n+1 sigma points around mean x with covariance P.
        Returns array of shape (2n+1, n).
        """
        n = self.n
        scale = math.sqrt((n + self._lam))
        try:
            L = np.linalg.cholesky((n + self._lam) * P)
        except np.linalg.LinAlgError:
            # Regularize if not positive definite
            P_reg = P + 1e-6 * np.eye(n)
            L = np.linalg.cholesky((n + self._lam) * P_reg)

        sigma = np.zeros((2 * n + 1, n))
        sigma[0] = x
        for i in range(n):
            sigma[i + 1]     = x + L[:, i]
            sigma[n + i + 1] = x - L[:, i]
        return sigma

    # ── state transition ──────────────────────────────────────────────────────

    def _f(self, x: np.ndarray) -> np.ndarray:
        """Constant-velocity model: x_{t+1} = F · x_t."""
        price, vel = x
        return np.array([price + vel * self.dt, vel])

    # ── observation model ─────────────────────────────────────────────────────

    def _h(self, x: np.ndarray) -> float:
        """Observe price (first state component)."""
        return float(x[0])

    # ── public interface ───────────────────────────────────────────────────────

    def initialize(self, price: float, velocity: float = 0.0) -> None:
        """Manually set initial state (optional — auto-initialized on first update)."""
        self._x = np.array([price, velocity])
        self._P = np.diag([25.0, 0.1])   # 5 USD / 0.316 vel uncertainty

    def update(self, z: float) -> UKFState:
        """
        Ingest a new price observation and return updated state.

        Parameters
        ----------
        z : observed price (e.g. from a DEX quote)

        Returns
        -------
        UKFState with posterior mean, covariance, innovation, anomaly flag
        """
        if z <= 0:
            raise ValueError(f"Price observation must be positive, got {z}")
        if self._x is None:
            # Auto-initialize on first observation
            self.initialize(z)
            return UKFState(self._x.copy(), self._P.copy(), 0.0, float(self.R), np.zeros(self.n), False)

        # ── Predict ──────────────────────────────────────────────────────────
        sigma_pts = self._sigma_points(self._x, self._P)
        sigma_pred = np.array([self._f(s) for s in sigma_pts])

        x_pred = sigma_pred.T @ self._Wm
        P_pred = self.Q.copy()
        for i, sp in enumerate(sigma_pred):
            diff = sp - x_pred
            P_pred += self._Wc[i] * np.outer(diff, diff)

        # ── Update ────────────────────────────────────────────────────────────
        sigma_obs = np.array([self._h(s) for s in sigma_pred])
        z_pred    = float(sigma_obs @ self._Wm)

        S = self.R
        for i, zo in enumerate(sigma_obs):
            S += self._Wc[i] * (zo - z_pred) ** 2

        Pxz = np.zeros(self.n)
        for i, sp in enumerate(sigma_pred):
            Pxz += self._Wc[i] * (sp - x_pred) * (sigma_obs[i] - z_pred)

        K   = Pxz / S                       # Kalman gain
        inn = z - z_pred                    # innovation

        x_new = x_pred + K * inn
        P_new = P_pred - np.outer(K, K) * S

        # Ensure positive definiteness
        P_new = 0.5 * (P_new + P_new.T) + 1e-8 * np.eye(self.n)

        self._x = x_new
        self._P = P_new

        # Mahalanobis distance for anomaly detection
        mahal = abs(inn) / math.sqrt(max(S, 1e-9))
        is_anomaly = mahal > self.anomaly_thresh

        return UKFState(
            mean=x_new.copy(),
            covariance=P_new.copy(),
            innovation=float(inn),
            innovation_cov=float(S),
            gain=K.copy(),
            is_anomaly=is_anomaly,
        )

    def predict_ahead(self, steps: int = 1) -> np.ndarray:
        """
        Predict state `steps` cycles into the future (without new observation).
        Returns predicted mean [price, velocity] array.
        """
        if self._x is None:
            raise RuntimeError("UKF not initialized — call update() first")
        x = self._x.copy()
        for _ in range(steps):
            x = self._f(x)
        return x

    @property
    def state(self) -> Optional[np.ndarray]:
        """Current state mean [price, velocity], or None if not initialized."""
        return self._x.copy() if self._x is not None else None

    @property
    def covariance(self) -> Optional[np.ndarray]:
        """Current state covariance, or None if not initialized."""
        return self._P.copy() if self._P is not None else None

