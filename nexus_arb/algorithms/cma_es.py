# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
CMA-ES — Covariance Matrix Adaptation Evolution Strategy.

Purpose: Find the optimal flash loan size for a given arbitrage path,
         accounting for non-linear slippage, gas costs, and loan fees.

Theory (Hansen 2006):
  CMA-ES maintains a multivariate Gaussian N(m, sigma^2 * C) over the
  search space and evolves it toward profitable regions via weighted
  recombination and covariance adaptation.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class CMAESResult:
    optimal_size_eth: float
    expected_profit_eth: float
    expected_profit_usd: float
    iterations: int
    converged: bool


class CMAES1D:
    """1-dimensional CMA-ES optimiser for trade size selection."""

    def __init__(
        self,
        population_size: int = 16,
        initial_sigma: float = 0.5,
        max_iterations: int = 100,
        tolerance: float = 1e-9,
        seed: Optional[int] = None
    ) -> None:
        self.lambda_ = max(population_size, 4)
        self.mu      = self.lambda_ // 2
        self.sigma0  = initial_sigma
        self.max_iter = max_iterations
        self.tol     = tolerance
        self.rng     = np.random.default_rng(seed)

        weights = np.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1))
        self.weights = weights / weights.sum()
        self.mueff   = 1.0 / (self.weights ** 2).sum()

        self.cs   = (self.mueff + 2) / (1 + self.mueff + 5)
        self.ds   = 1 + 2 * max(0, math.sqrt((self.mueff - 1) / 2) - 1) + self.cs
        self.cc   = (4 + self.mueff) / 5
        self.c1   = 2 / ((1 + 0.3) ** 2 + self.mueff)
        self.cmu  = min(1 - self.c1,
                        2 * (self.mueff - 2 + 1 / self.mueff) /
                        ((1 + 1.3) ** 2 + self.mueff))
        self.chiN = math.sqrt(1) * (1 - 1 / 4 + 1 / 21)

    def optimize(
        self,
        profit_fn: Callable[[float], float],
        x_min: float = 0.01,
        x_max: float = 500.0,
        x_start: Optional[float] = None,
        eth_price_usd: float = 3000.0
    ) -> CMAESResult:
        x_start  = x_start or (x_min + x_max) / 3
        log_min  = math.log(max(x_min, 1e-6))
        log_max  = math.log(x_max)
        log_x0   = math.log(max(x_start, x_min))

        m     = log_x0
        sigma = self.sigma0
        pc    = 0.0
        ps    = 0.0
        C     = 1.0

        best_x      = x_start
        best_profit = profit_fn(x_start)
        converged   = False

        for gen in range(self.max_iter):
            zk       = self.rng.standard_normal(self.lambda_)
            dk       = math.sqrt(C) * zk
            xk_log   = np.clip(m + sigma * dk, log_min, log_max)
            xk       = np.exp(xk_log)
            fk       = np.array([profit_fn(x) for x in xk])
            fk_neg   = -fk
            order    = np.argsort(fk_neg)
            xk_sorted = xk_log[order]
            zk_sorted = zk[order]
            fk_sorted = fk[order[::-1]]

            if fk_sorted[0] > best_profit:
                best_profit = fk_sorted[0]
                best_x      = xk[order[0]]

            m_old = m
            m     = float(np.dot(self.weights, xk_sorted[:self.mu]))

            C_inv_sqrt = 1.0 / math.sqrt(C) if C > 0 else 1.0
            ps = ((1 - self.cs) * ps
                  + math.sqrt(self.cs * (2 - self.cs) * self.mueff)
                  * C_inv_sqrt
                  * float(np.dot(self.weights, zk_sorted[:self.mu])))

            hsig = (abs(ps) / math.sqrt(1 - (1 - self.cs) ** (2 * (gen + 1)))
                    / self.chiN < 1.4 + 2 / 2)

            pc = ((1 - self.cc) * pc
                  + (1 if hsig else 0)
                  * math.sqrt(self.cc * (2 - self.cc) * self.mueff)
                  * math.sqrt(C)
                  * float(np.dot(self.weights, zk_sorted[:self.mu])))

            rank_one = self.c1 * pc ** 2
            rank_mu  = self.cmu * float(np.dot(
                self.weights,
                (math.sqrt(C) * zk_sorted[:self.mu]) ** 2
            ))
            C = (1 - self.c1 - self.cmu) * C + rank_one + rank_mu

            exp_arg = (self.cs / self.ds) * (abs(ps) / self.chiN - 1)
            sigma *= math.exp(max(min(exp_arg, 20.0), -20.0))  # clamp exponent to prevent float overflow
            # Clamp sigma to [1e-10, log_max-log_min]: must stay within the log-space
            # search bounds and must not shrink to zero (which would cause degenerate sampling)
            sigma  = max(min(sigma, log_max - log_min), 1e-10)

            if sigma < self.tol or abs(m - m_old) < 1e-12:
                converged = True
                break

        final_x      = float(np.exp(np.clip(m, log_min, log_max)))
        final_profit = profit_fn(final_x)
        if final_profit > best_profit:
            best_x      = final_x
            best_profit = final_profit

        return CMAESResult(
            optimal_size_eth=best_x,
            expected_profit_eth=best_profit,
            expected_profit_usd=best_profit * eth_price_usd,
            iterations=gen + 1,
            converged=converged
        )


class TradeOptimizer:
    """Uses CMA-ES to find optimal trade size for an arbitrage opportunity."""

    def __init__(self, config: dict) -> None:
        cma_cfg = config.get("algorithms", {}).get("cma_es", {})
        self.cma = CMAES1D(
            population_size=cma_cfg.get("population_size", 32),
            initial_sigma=cma_cfg.get("initial_sigma", 0.3),
            max_iterations=cma_cfg.get("max_iterations", 100),
            tolerance=cma_cfg.get("tolerance", 1e-9)
        )
        self.flash_fee = config.get("trading", {}).get("flash_loan_fee_bps", 9) / 10_000

    def build_profit_function(
        self,
        opportunity,
        gas_cost_eth: float,
        eth_price_usd: float = 3000.0
    ) -> Callable[[float], float]:
        pools = opportunity.pools
        flash_fee = self.flash_fee

        def simulate_path(amount_in_eth: float) -> float:
            amount = amount_in_eth
            for pool in pools:
                if pool.liquidity <= 0:
                    return -float("inf")
                fee_mult = 1 - pool.fee_bps / 10_000
                slippage = amount / (pool.liquidity + amount)
                effective_rate = pool.price * fee_mult * (1 - slippage)
                amount = amount * effective_rate
            repay = amount_in_eth * (1 + flash_fee)
            return amount - repay - gas_cost_eth

        return simulate_path

    def optimize(
        self,
        opportunity,
        gas_cost_eth: float,
        eth_price_usd: float,
        min_size: float,
        max_size: float
    ) -> CMAESResult:
        profit_fn = self.build_profit_function(opportunity, gas_cost_eth, eth_price_usd)
        start = min(opportunity.max_input_eth, max_size)
        return self.cma.optimize(
            profit_fn,
            x_min=min_size,
            x_max=max_size,
            x_start=start,
            eth_price_usd=eth_price_usd
        )
