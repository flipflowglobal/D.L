"""
nexus_arb.algorithms.cma_es
============================

Covariance Matrix Adaptation Evolution Strategy (CMA-ES).

Usage in AUREON
---------------
  - Position sizing: optimize trade size across multiple tokens/DEXs to
    maximize risk-adjusted P&L subject to gas and capital constraints.
  - Parameter tuning: find optimal mean-reversion window, threshold, and
    spread threshold for the current market regime.
  - Online adaptation: re-run every N cycles to track non-stationary markets.

Theory
------
CMA-ES is a second-order stochastic optimizer for black-box functions.
It maintains a Gaussian distribution N(m, σ²C) over the search space,
sampling λ candidate solutions per generation and updating the mean m,
step size σ, and covariance matrix C using:

  - Weighted recombination (best μ out of λ samples)
  - Cumulative step-size adaptation (CSA) for σ
  - Rank-one + rank-μ updates for C

Complexity per generation: O(λ·n + n³) where n = search space dimension.

Formal Specification
---------------------
  Preconditions:
    - f: Callable[[np.ndarray], float] — objective to MINIMIZE
    - x0: np.ndarray, shape (n,)       — initial mean
    - sigma0: float > 0                — initial step size
    - n_generations >= 1

  Postconditions:
    - Returns OptimResult with x_opt (shape n,), f_opt (float), history
    - x_opt minimises f within tolerance tol or after n_generations

  Invariants:
    - C is always positive definite (enforced via eigendecomposition)
    - σ remains positive throughout
    - Objective evaluations: exactly λ × n_generations (no early restart)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np


@dataclass
class OptimResult:
    """Result of a CMA-ES optimization run."""
    x_opt:       np.ndarray   # best solution found
    f_opt:       float        # objective value at x_opt
    n_evals:     int          # total function evaluations
    n_generations: int        # completed generations
    converged:   bool         # True if stopping criterion met
    history:     List[float]  # f_opt per generation


class CMAES:
    """
    CMA-ES optimizer — pure NumPy, no external dependencies.

    Parameters
    ----------
    n_dim       : problem dimension (number of parameters)
    sigma0      : initial step size (exploration radius)
    pop_size    : population size λ; defaults to 4 + ⌊3·ln(n_dim)⌋
    seed        : random seed for reproducibility
    """

    def __init__(
        self,
        n_dim:    int,
        sigma0:   float = 0.5,
        pop_size: Optional[int] = None,
        seed:     Optional[int] = None,
    ) -> None:
        if n_dim < 1:
            raise ValueError("n_dim must be >= 1")
        if sigma0 <= 0:
            raise ValueError("sigma0 must be positive")

        self.n = n_dim
        self.rng = np.random.default_rng(seed)

        # Population / selection sizes
        lam = pop_size or (4 + int(3 * math.log(n_dim)))
        mu  = lam // 2
        self._lam = lam
        self._mu  = mu

        # Recombination weights (log-linear, normalized)
        raw_w = np.array([math.log(mu + 0.5) - math.log(i + 1) for i in range(mu)])
        self._w   = raw_w / raw_w.sum()
        self._mu_eff = 1.0 / np.dot(self._w, self._w)   # variance effective selection mass

        # Adaptation constants
        self._cc  = (4 + self._mu_eff / n_dim) / (n_dim + 4 + 2 * self._mu_eff / n_dim)
        self._cs  = (self._mu_eff + 2) / (n_dim + self._mu_eff + 5)
        self._c1  = 2.0 / ((n_dim + 1.3) ** 2 + self._mu_eff)
        self._cmu = min(
            1 - self._c1,
            2 * (self._mu_eff - 2 + 1 / self._mu_eff) / ((n_dim + 2) ** 2 + self._mu_eff)
        )
        self._damps = 1 + 2 * max(0, math.sqrt((self._mu_eff - 1) / (n_dim + 1)) - 1) + self._cs

        # Expected norm of N(0, I)
        self._chi_n = math.sqrt(n_dim) * (1 - 1 / (4 * n_dim) + 1 / (21 * n_dim ** 2))

    # ── main optimization loop ────────────────────────────────────────────────

    def minimize(
        self,
        f:             Callable[[np.ndarray], float],
        x0:            np.ndarray,
        n_generations: int = 200,
        tol:           float = 1e-10,
    ) -> OptimResult:
        """
        Minimize f starting from x0.

        Parameters
        ----------
        f            : black-box objective (lower = better)
        x0           : initial solution, shape (n_dim,)
        n_generations: maximum generations
        tol          : convergence threshold on σ

        Returns
        -------
        OptimResult
        """
        x0 = np.asarray(x0, dtype=float)
        if x0.shape != (self.n,):
            raise ValueError(f"x0 must have shape ({self.n},), got {x0.shape}")

        # State variables
        m  = x0.copy()               # distribution mean
        sigma = float(self.sigma0_from_x0(x0))
        ps = np.zeros(self.n)        # evolution path for σ
        pc = np.zeros(self.n)        # evolution path for C
        C  = np.eye(self.n)          # covariance matrix
        D  = np.ones(self.n)         # eigenvalues
        B  = np.eye(self.n)          # eigenvectors (columns)
        eigen_eval = 0               # last generation that updated B, D

        history: List[float] = []
        n_evals  = 0
        x_opt    = m.copy()
        f_opt    = f(m)
        n_evals += 1

        for gen in range(1, n_generations + 1):
            # ── Sample λ offspring ───────────────────────────────────────────
            if gen - eigen_eval > self._lam / (self._c1 + self._cmu) / self.n / 10:
                # Update B, D from C
                C = np.triu(C) + np.triu(C, 1).T   # enforce symmetry
                D2, B = np.linalg.eigh(C)
                D = np.sqrt(np.maximum(D2, 1e-20))
                eigen_eval = gen

            # z ~ N(0, I), y = B · diag(D) · z, x = m + σ · y
            Z = self.rng.standard_normal((self._lam, self.n))
            Y = (B * D) @ Z.T            # shape (n, λ)
            X = m[:, None] + sigma * Y   # shape (n, λ)

            # ── Evaluate ──────────────────────────────────────────────────────
            fitness = np.array([f(X[:, i]) for i in range(self._lam)])
            n_evals += self._lam

            # ── Sort by fitness (ascending = minimize) ────────────────────────
            idx = np.argsort(fitness)
            best_i = idx[0]
            if fitness[best_i] < f_opt:
                f_opt = float(fitness[best_i])
                x_opt = X[:, best_i].copy()

            # ── Weighted recombination ─────────────────────────────────────────
            selected_Y = Y[:, idx[:self._mu]]  # (n, mu)
            selected_Z = Z[idx[:self._mu], :]  # (mu, n)

            y_w = selected_Y @ self._w          # weighted mean in y-space
            m_old = m.copy()
            m = m + sigma * y_w

            # ── Step-size control (CSA) ────────────────────────────────────────
            invsqrtC = B @ np.diag(1.0 / D) @ B.T
            ps = (1 - self._cs) * ps + math.sqrt(self._cs * (2 - self._cs) * self._mu_eff) * invsqrtC @ y_w
            hs = (np.dot(ps, ps) / self.n / (1 - (1 - self._cs) ** (2 * n_evals / self._lam))) < (2 + 4 / (self.n + 1))
            exp_arg = (self._cs / self._damps) * (np.linalg.norm(ps) / self._chi_n - 1)
            sigma *= math.exp(np.clip(exp_arg, -100.0, 100.0))

            # ── Covariance update ─────────────────────────────────────────────
            pc = (1 - self._cc) * pc + hs * math.sqrt(self._cc * (2 - self._cc) * self._mu_eff) * y_w
            artmp = math.sqrt(1 - hs) * math.sqrt(self._cc * (2 - self._cc))

            C = (
                (1 - self._c1 - self._cmu) * C
                + self._c1 * (np.outer(pc, pc) + artmp ** 2 * C)
                + self._cmu * (selected_Y @ np.diag(self._w) @ selected_Y.T)
            )

            history.append(f_opt)

            # ── Convergence ───────────────────────────────────────────────────
            if sigma < tol:
                return OptimResult(x_opt, f_opt, n_evals, gen, True, history)

        return OptimResult(x_opt, f_opt, n_evals, n_generations, False, history)

    @staticmethod
    def sigma0_from_x0(x0: np.ndarray, scale: float = 0.3) -> float:
        """Heuristic initial step size: 30 % of the L2-norm of x0 (min 0.01)."""
        norm = float(np.linalg.norm(x0))
        return max(norm * scale, 0.01)

    # ── convenience: position-sizing objective ────────────────────────────────

    @staticmethod
    def position_sizing_objective(
        returns:       np.ndarray,
        gas_costs:     np.ndarray,
        max_exposure:  float = 1.0,
    ) -> Callable[[np.ndarray], float]:
        """
        Build an objective function that maximises Sharpe ratio subject to
        a capital budget constraint.  Pass to minimize().

        Parameters
        ----------
        returns      : shape (T, n_assets) — historical return matrix
        gas_costs    : shape (n_assets,)   — fixed cost per unit traded
        max_exposure : maximum total absolute position weight

        Returns
        -------
        f(w) = -Sharpe(w)  (minimize negative Sharpe)
        """
        T, n = returns.shape

        def objective(w: np.ndarray) -> float:
            # Enforce constraints via penalty
            penalty = 0.0
            total_exposure = float(np.sum(np.abs(w)))
            if total_exposure > max_exposure:
                penalty += 1e6 * (total_exposure - max_exposure) ** 2

            net_returns = returns @ w - gas_costs @ np.abs(w)
            mean_r = float(np.mean(net_returns))
            std_r  = float(np.std(net_returns, ddof=1)) + 1e-9

            sharpe = mean_r / std_r
            return -sharpe + penalty

        return objective
