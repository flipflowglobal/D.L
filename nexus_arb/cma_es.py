# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/cma_es.py — BIPOP-CMA-ES with surrogate MLP.

CMA-ES: N(m, sigma^2 * C)
Surrogate: 2-layer MLP for pre-screening candidates.
BIPOP: large (IPOP doubling) and small restart regimes.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("nexus_arb.cma_es")


@dataclass
class OptimResult:
    x_opt: np.ndarray
    f_opt: float
    n_evals: int
    n_iters: int
    converged: bool
    history: List[float] = field(default_factory=list)


class _SurrogateMLP:
    """2-layer MLP: n -> 32 -> 16 -> 1 with ReLU, trained by SGD."""

    def __init__(self, n_in: int, lr: float = 0.01) -> None:
        self._n = n_in
        self._lr = lr
        rng = np.random.default_rng(0)
        self._W1 = rng.normal(0, math.sqrt(2.0 / max(n_in, 1)), (32, n_in))
        self._b1 = np.zeros(32)
        self._W2 = rng.normal(0, math.sqrt(2.0 / 32), (16, 32))
        self._b2 = np.zeros(16)
        self._W3 = rng.normal(0, math.sqrt(2.0 / 16), (1, 16))
        self._b3 = np.zeros(1)
        self._trained = False

    def _relu(self, x: np.ndarray) -> np.ndarray:
        return np.maximum(0.0, x)

    def _drelu(self, x: np.ndarray) -> np.ndarray:
        return (x > 0).astype(float)

    def forward(self, x: np.ndarray) -> float:
        h1 = self._relu(self._W1 @ x + self._b1)
        h2 = self._relu(self._W2 @ h1 + self._b2)
        return float((self._W3 @ h2 + self._b3)[0])

    def train(self, X: np.ndarray, y: np.ndarray, n_epochs: int = 20) -> None:
        if len(X) < 4:
            return
        N = len(X)
        for _ in range(n_epochs):
            perm = np.random.permutation(N)
            for i in perm:
                x_i = X[i]
                y_i = float(y[i])
                z1 = self._W1 @ x_i + self._b1
                h1 = self._relu(z1)
                z2 = self._W2 @ h1 + self._b2
                h2 = self._relu(z2)
                z3 = self._W3 @ h2 + self._b3
                pred = float(z3[0])
                d_pred = 2.0 * (pred - y_i)
                dW3 = d_pred * h2[None, :]
                db3 = np.array([d_pred])
                d_h2 = (self._W3.T * d_pred).flatten()
                d_z2 = d_h2 * self._drelu(z2)
                dW2 = np.outer(d_z2, h1)
                db2 = d_z2
                d_h1 = self._W2.T @ d_z2
                d_z1 = d_h1 * self._drelu(z1)
                dW1 = np.outer(d_z1, x_i)
                db1 = d_z1
                lr = self._lr
                self._W3 -= lr * dW3
                self._b3 -= lr * db3
                self._W2 -= lr * dW2
                self._b2 -= lr * db2
                self._W1 -= lr * dW1
                self._b1 -= lr * db1
        self._trained = True

    def rank(self, candidates: np.ndarray) -> np.ndarray:
        if not self._trained or len(candidates) == 0:
            return np.arange(len(candidates))
        preds = np.array([self.forward(c) for c in candidates])
        return np.argsort(preds)


class BIPOPCMAESOptimiser:
    """BIPOP-CMA-ES with surrogate MLP pre-screening."""

    def __init__(self, n: int, sigma0: float = 0.3, max_evals: int = 10_000,
                 tol: float = 1e-8, popsize: int = 0, use_surrogate: bool = True,
                 bounds: Optional[Tuple[np.ndarray, np.ndarray]] = None,
                 event_bus=None) -> None:
        self._n = n
        self._sig0 = sigma0
        self._maxev = max_evals
        self._tol = tol
        self._bounds = bounds
        self._use_surr = use_surrogate
        self._bus = event_bus
        self._lam0 = popsize if popsize > 0 else int(4 + math.floor(3 * math.log(max(n, 1))))
        self._surrogate = _SurrogateMLP(n) if use_surrogate else None
        self._budget_large = max_evals // 2
        self._budget_small = max_evals - self._budget_large

    def _run_cmaes(self, f: Callable[[np.ndarray], float], x0: np.ndarray,
                   sigma0: float, lam: int, budget: int) -> Tuple[np.ndarray, float, int, List[float]]:
        n = self._n
        mu = max(lam // 2, 1)
        raw_w = np.array([math.log(lam / 2.0 + 1) - math.log(i + 1) for i in range(mu)])
        raw_w = np.maximum(raw_w, 0.0)
        w_sum = float(raw_w.sum())
        if w_sum <= 0:
            w_sum = 1.0
        w = raw_w / w_sum
        mu_eff = 1.0 / float(np.sum(w ** 2))
        c_sig = (mu_eff + 2.0) / (n + mu_eff + 5.0)
        d_sig = 1.0 + 2.0 * max(0.0, math.sqrt((mu_eff - 1.0) / (n + 1.0)) - 1.0) + c_sig
        c_c = (4.0 + mu_eff / n) / (n + 4.0 + 2.0 * mu_eff / n)
        c_1 = 2.0 / ((n + 1.3) ** 2 + mu_eff)
        c_mu = min(1.0 - c_1, 2.0 * (mu_eff - 2.0 + 1.0 / mu_eff) / ((n + 2.0) ** 2 + mu_eff))
        chi_n = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n * n))
        m = x0.copy().astype(float)
        sigma = float(sigma0)
        p_sig = np.zeros(n)
        p_c = np.zeros(n)
        C = np.eye(n)
        eig_vals = np.ones(n)
        eig_vecs = np.eye(n)
        eig_counter = 0
        n_evals = 0
        history: List[float] = []
        f_opt = float("inf")
        x_opt = m.copy()
        surr_X: List[np.ndarray] = []
        surr_y: List[float] = []
        while n_evals < budget:
            candidates = []
            for _ in range(lam):
                z = np.random.randn(n)
                y = eig_vecs @ (eig_vals * z)
                x = m + sigma * y
                if self._bounds is not None:
                    x = np.clip(x, self._bounds[0], self._bounds[1])
                candidates.append((x, z, y))
            if self._use_surr and self._surrogate is not None and self._surrogate._trained:
                cand_arr = np.array([c[0] for c in candidates])
                order = self._surrogate.rank(cand_arr)
                candidates = [candidates[i] for i in order]
            evals: List[Tuple[float, np.ndarray, np.ndarray]] = []
            for x, z, y in candidates:
                fval = float(f(x))
                n_evals += 1
                evals.append((fval, x, y))
                surr_X.append(x.copy())
                surr_y.append(fval)
                if n_evals >= budget:
                    break
            evals.sort(key=lambda e: e[0])
            if not evals:
                break
            f_best = evals[0][0]
            history.append(f_best)
            if f_best < f_opt:
                f_opt = f_best
                x_opt = evals[0][1].copy()
            if self._use_surr and self._surrogate is not None and len(surr_X) >= 4:
                if n_evals % max(10 * lam, 1) < lam:
                    SX = np.array(surr_X[-200:])
                    Sy = np.array(surr_y[-200:])
                    self._surrogate.train(SX, Sy)
            mu_eff_cands = evals[:mu]
            y_w = sum(float(w[i]) * mu_eff_cands[i][2] for i in range(len(mu_eff_cands)))
            m = m + sigma * y_w
            C_invsqrt = eig_vecs @ np.diag(1.0 / np.maximum(eig_vals, 1e-12)) @ eig_vecs.T
            p_sig = (1.0 - c_sig) * p_sig + math.sqrt(c_sig * (2.0 - c_sig) * mu_eff) * (C_invsqrt @ y_w)
            norm_ps = float(np.linalg.norm(p_sig))
            denom = math.sqrt(1.0 - (1.0 - c_sig) ** (2.0 * (n_evals / lam + 1)))
            hs = 1.0 if denom < 1e-12 else float(norm_ps / denom / chi_n < 1.4 + 2.0 / (n + 1.0))
            sigma = sigma * math.exp((c_sig / d_sig) * (norm_ps / chi_n - 1.0))
            sigma = max(min(sigma, 1e6), 1e-12)
            p_c = (1.0 - c_c) * p_c + hs * math.sqrt(c_c * (2.0 - c_c) * mu_eff) * y_w
            C = ((1.0 - c_1 - c_mu) * C
                 + c_1 * np.outer(p_c, p_c)
                 + c_mu * sum(float(w[i]) * np.outer(mu_eff_cands[i][2], mu_eff_cands[i][2])
                              for i in range(len(mu_eff_cands))))
            C = 0.5 * (C + C.T)
            eig_counter += 1
            if eig_counter >= max(1, n // (10 * lam) + 1):
                eig_counter = 0
                try:
                    eig_vals_raw, eig_vecs = np.linalg.eigh(C)
                    eig_vals = np.sqrt(np.maximum(eig_vals_raw, 1e-20))
                except np.linalg.LinAlgError:
                    pass
            if sigma < self._tol or f_opt < self._tol:
                break
        return x_opt, f_opt, n_evals, history

    def optimise(self, f: Callable[[np.ndarray], float], x0: np.ndarray) -> OptimResult:
        x0 = np.asarray(x0, dtype=float)
        best_x = x0.copy()
        best_f = float("inf")
        best_hist: List[float] = []
        total_ev = 0
        lam_large = self._lam0
        restart = 0
        n_iters = 0
        converged = False
        while total_ev < self._maxev:
            regime = "large" if restart % 2 == 0 else "small"
            if regime == "large":
                lam = lam_large
                sigma = self._sig0
                if restart > 0:
                    lam_large = lam_large * 2
                budget = min(self._budget_large, self._maxev - total_ev)
            else:
                lam = max(self._lam0 // 2, 2)
                sigma = float(np.random.uniform(0.0, 0.2 * self._sig0))
                budget = min(self._budget_small // max(restart, 1), self._maxev - total_ev)
            if budget <= 0:
                break
            start = x0 + np.random.randn(self._n) * self._sig0 * (restart ** 0.5)
            if self._bounds is not None:
                start = np.clip(start, self._bounds[0], self._bounds[1])
            x_r, f_r, ev_r, hist_r = self._run_cmaes(f, start, sigma, lam, budget)
            total_ev += ev_r
            n_iters += len(hist_r)
            if f_r < best_f:
                best_f = f_r
                best_x = x_r.copy()
                best_hist = hist_r
            if f_r < self._tol:
                converged = True
                break
            restart += 1
        return OptimResult(x_opt=best_x, f_opt=best_f, n_evals=total_ev,
                           n_iters=n_iters, converged=converged, history=best_hist)
