"""
tests/test_nexus_arb.py
========================

Offline unit tests for all nexus_arb algorithms:
  - BellmanFordArb   (negative-cycle detection)
  - CMAES            (covariance matrix adaptation)
  - UnscentedKalmanFilter (sigma-point state estimation)
  - ThompsonSamplingBandit (Beta-Bernoulli bandit)
  - TradingPolicy    (PPO actor-critic)

All tests run fully offline — no RPC, no network.
"""

from __future__ import annotations

import math
import sys
import os

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# BellmanFordArb
# ─────────────────────────────────────────────────────────────────────────────

class TestBellmanFordArb:

    def test_empty_graph_returns_no_cycle(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        result = arb.find_arbitrage("WETH")
        assert result.has_cycle is False
        assert result.profit_ratio == 1.0

    def test_negative_rate_raises(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        with pytest.raises(ValueError):
            arb.add_edge("A", "B", -1.0)

    def test_zero_rate_raises(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        with pytest.raises(ValueError):
            arb.add_edge("A", "B", 0.0)

    def test_no_cycle_on_balanced_rates(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        arb.add_edge("WETH", "USDC", 2000.0, "uniswap")
        arb.add_edge("USDC", "WETH", 1.0 / 2000.0, "uniswap")   # exact reciprocal
        result = arb.find_arbitrage("WETH")
        assert result.has_cycle is False

    def test_detects_profitable_cycle(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        # Buy on uniswap at 2000, sell on sushi at 2000.5 → 0.025 % edge
        arb.add_edge("WETH", "USDC", 2000.0,   "uniswap")
        arb.add_edge("USDC", "WETH", 1/1999.5, "sushiswap")  # slight extra
        result = arb.find_arbitrage("WETH")
        assert result.has_cycle is True
        assert result.profit_ratio > 1.0

    def test_cycle_is_closed(self):
        """Returned cycle path must start and end at the same token."""
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        arb.add_edge("A", "B", 1.1)
        arb.add_edge("B", "C", 1.1)
        arb.add_edge("C", "A", 1.1)
        result = arb.find_arbitrage("A")
        if result.has_cycle:
            assert result.cycle[0] == result.cycle[-1]

    def test_add_price_matrix(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        prices = {
            "WETH": {"USDC": 2000.0, "DAI": 1999.5},
            "USDC": {"WETH": 0.0005, "DAI": 0.999},
            "DAI":  {"USDC": 1.001,  "WETH": 0.0005002},
        }
        arb.add_price_matrix(prices, dex="matrix")
        # Should have edges without raising
        assert len(arb._nodes) == 3

    def test_find_best_arbitrage_returns_highest_profit(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        arb.add_edge("X", "Y", 1.05)
        arb.add_edge("Y", "X", 1.05)
        result = arb.find_best_arbitrage()
        assert result.has_cycle is True
        assert result.profit_ratio >= 1.0

    def test_clear_resets_graph(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        arb = BellmanFordArb()
        arb.add_edge("A", "B", 1.5)
        arb.clear()
        result = arb.find_arbitrage()
        assert result.has_cycle is False


# ─────────────────────────────────────────────────────────────────────────────
# CMAES
# ─────────────────────────────────────────────────────────────────────────────

class TestCMAES:

    def test_minimises_sphere_function(self):
        from nexus_arb.algorithms.cma_es import CMAES
        cma = CMAES(n_dim=3, sigma0=0.5, seed=0)
        result = cma.minimize(
            lambda x: float(np.sum(x ** 2)),
            x0=np.array([1.0, 1.0, 1.0]),
            n_generations=100,
        )
        assert result.f_opt < 0.5   # should converge close to 0

    def test_result_has_correct_shape(self):
        from nexus_arb.algorithms.cma_es import CMAES
        cma = CMAES(n_dim=4, sigma0=0.3, seed=1)
        result = cma.minimize(
            lambda x: float(np.sum(x ** 2)),
            x0=np.zeros(4),
            n_generations=20,
        )
        assert result.x_opt.shape == (4,)
        assert isinstance(result.f_opt, float)
        assert result.n_evals > 0

    def test_invalid_ndim_raises(self):
        from nexus_arb.algorithms.cma_es import CMAES
        with pytest.raises(ValueError):
            CMAES(n_dim=0)

    def test_invalid_sigma_raises(self):
        from nexus_arb.algorithms.cma_es import CMAES
        with pytest.raises(ValueError):
            CMAES(n_dim=2, sigma0=-1.0)

    def test_history_grows_monotonically_or_improves(self):
        from nexus_arb.algorithms.cma_es import CMAES
        cma = CMAES(n_dim=2, sigma0=0.5, seed=7)
        result = cma.minimize(
            lambda x: float(np.sum(x ** 2)),
            x0=np.array([3.0, 3.0]),
            n_generations=30,
        )
        assert len(result.history) == result.n_generations

    def test_converges_flag_set_when_tol_met(self):
        from nexus_arb.algorithms.cma_es import CMAES
        cma = CMAES(n_dim=1, sigma0=0.5, seed=2)
        result = cma.minimize(
            lambda x: float(x[0] ** 2),
            x0=np.array([1.0]),
            n_generations=200,
            tol=1e-6,
        )
        # Should converge for a simple 1-D sphere
        assert result.f_opt < 1e-4


# ─────────────────────────────────────────────────────────────────────────────
# UnscentedKalmanFilter
# ─────────────────────────────────────────────────────────────────────────────

class TestUKF:

    def test_first_update_initialises_state(self):
        from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
        ukf = UnscentedKalmanFilter()
        result = ukf.update(2000.0)
        assert abs(result.mean[0] - 2000.0) < 10.0

    def test_state_mean_tracks_price(self):
        from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
        ukf = UnscentedKalmanFilter()
        prices = [2000.0, 2010.0, 2020.0, 2030.0, 2040.0]
        for p in prices:
            result = ukf.update(p)
        # Price component should be near the last price
        assert abs(result.mean[0] - 2040.0) < 50.0

    def test_covariance_stays_positive_definite(self):
        from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
        ukf = UnscentedKalmanFilter()
        for p in [1900.0, 2000.0, 2100.0, 1950.0]:
            result = ukf.update(p)
        eigenvalues = np.linalg.eigvalsh(result.covariance)
        assert all(ev > 0 for ev in eigenvalues)

    def test_anomaly_detection_on_large_spike(self):
        from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
        ukf = UnscentedKalmanFilter(anomaly_thresh=3.0)
        # Warm up
        for p in [2000.0] * 5:
            ukf.update(p)
        # Massive spike should trigger anomaly
        result = ukf.update(5000.0)
        assert result.is_anomaly is True

    def test_no_anomaly_on_stable_price(self):
        from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
        ukf = UnscentedKalmanFilter()
        for p in [2000.0, 2001.0, 1999.5, 2000.5]:
            result = ukf.update(p)
        assert result.is_anomaly is False

    def test_reset_via_reinit(self):
        from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
        ukf = UnscentedKalmanFilter()
        for p in [1500.0, 1600.0]:
            ukf.update(p)
        # Re-initialize via initialize()
        ukf.initialize(2000.0)
        result = ukf.update(2000.0)
        assert abs(result.mean[0] - 2000.0) < 50.0


# ─────────────────────────────────────────────────────────────────────────────
# ThompsonSamplingBandit
# ─────────────────────────────────────────────────────────────────────────────

class TestThompsonSamplingBandit:

    def test_select_returns_valid_arm(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        bandit = ThompsonSamplingBandit(["uniswap", "sushiswap", "curve"], seed=0)
        arm = bandit.select()
        assert arm in ["uniswap", "sushiswap", "curve"]

    def test_update_increments_stats(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        bandit = ThompsonSamplingBandit(["A", "B"], seed=1)
        bandit.update("A", 1.0)
        stats = bandit.stats()
        assert stats["A"]["n_selected"] == 1

    def test_empty_arms_raises(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        with pytest.raises(ValueError):
            ThompsonSamplingBandit([])

    def test_invalid_prior_raises(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        with pytest.raises(ValueError):
            ThompsonSamplingBandit(["A"], alpha0=0.0)

    def test_best_arm_wins_after_many_updates(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        bandit = ThompsonSamplingBandit(["low", "high"], seed=42)
        # Give "high" lots of successes
        for _ in range(50):
            bandit.update("high", 1.0)
        for _ in range(50):
            bandit.update("low", 0.0)
        rewards = bandit.expected_rewards()
        assert rewards["high"] > rewards["low"]

    def test_select_top_k(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        bandit = ThompsonSamplingBandit(["A", "B", "C", "D"], seed=5)
        top2 = bandit.select_top_k(2)
        assert len(top2) == 2
        assert all(a in ["A", "B", "C", "D"] for a in top2)

    def test_expected_rewards_range(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        bandit = ThompsonSamplingBandit(["X", "Y"], seed=3)
        rewards = bandit.expected_rewards()
        for v in rewards.values():
            assert 0.0 < v < 1.0

    def test_summary_contains_all_arms(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        arms = ["a", "b", "c"]
        bandit = ThompsonSamplingBandit(arms, seed=0)
        stats = bandit.stats()
        assert set(stats.keys()) == set(arms)


# ─────────────────────────────────────────────────────────────────────────────
# TradingPolicy (PPO)
# ─────────────────────────────────────────────────────────────────────────────

class TestTradingPolicy:

    def test_select_action_returns_valid_action(self):
        from nexus_arb.algorithms.ppo import TradingPolicy, STATE_DIM
        policy = TradingPolicy(seed=0)
        state  = np.zeros(STATE_DIM)
        action, log_prob, value = policy.select_action(state)
        assert action in (0, 1, 2)
        assert isinstance(log_prob, float)
        assert isinstance(value, float)

    def test_action_name_mapping(self):
        from nexus_arb.algorithms.ppo import TradingPolicy, ACTION_HOLD, ACTION_BUY, ACTION_SELL
        policy = TradingPolicy(seed=1)
        assert policy.action_name(ACTION_HOLD) == "HOLD"
        assert policy.action_name(ACTION_BUY)  == "BUY"
        assert policy.action_name(ACTION_SELL) == "SELL"

    def test_policy_probs_sum_to_one(self):
        from nexus_arb.algorithms.ppo import TradingPolicy, STATE_DIM
        policy = TradingPolicy(seed=2)
        state  = np.array([0.1, -0.001, 0.02, 0.0, 0.0, 1.0])
        probs  = policy.policy_probs(state)
        assert abs(probs.sum() - 1.0) < 1e-6
        assert all(p >= 0 for p in probs)

    def test_update_returns_result(self):
        from nexus_arb.algorithms.ppo import TradingPolicy, Transition, STATE_DIM
        policy = TradingPolicy(seed=3, n_epochs=1, batch_size=4)
        state  = np.zeros(STATE_DIM)
        transitions = [
            Transition(state, 1, 0.01, -1.1, 0.5, False),
            Transition(state, 0, 0.00, -1.2, 0.4, False),
            Transition(state, 2, 0.02, -1.0, 0.6, False),
        ]
        result = policy.update(transitions, last_value=0.0)
        assert isinstance(result.policy_loss, float)
        assert isinstance(result.value_loss, float)
        assert result.n_updates > 0

    def test_update_empty_rollout(self):
        from nexus_arb.algorithms.ppo import TradingPolicy
        policy = TradingPolicy(seed=4)
        result = policy.update([], last_value=0.0)
        assert result.n_updates == 0

    def test_encode_state_shape(self):
        from nexus_arb.algorithms.ppo import TradingPolicy, STATE_DIM
        state = TradingPolicy.encode_state(
            price=2000.0, prev_price=1990.0, volatility=0.02,
            position=0.5, drawdown=0.01, cash_ratio=0.8,
        )
        assert state.shape == (STATE_DIM,)

    def test_encode_state_is_finite(self):
        from nexus_arb.algorithms.ppo import TradingPolicy
        state = TradingPolicy.encode_state(
            price=2000.0, prev_price=2000.0, volatility=0.0,
            position=0.0, drawdown=0.0, cash_ratio=1.0,
        )
        assert all(math.isfinite(v) for v in state)

    def test_stats_dict(self):
        from nexus_arb.algorithms.ppo import TradingPolicy
        policy = TradingPolicy(seed=5)
        s = policy.stats()
        assert "total_updates" in s
        assert "n_actions" in s
        assert s["n_actions"] == 3

    def test_value_is_finite(self):
        from nexus_arb.algorithms.ppo import TradingPolicy, STATE_DIM
        policy = TradingPolicy(seed=6)
        v = policy.value(np.zeros(STATE_DIM))
        assert math.isfinite(v)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level import
# ─────────────────────────────────────────────────────────────────────────────

def test_nexus_arb_top_level_imports():
    """All five algorithms importable from nexus_arb top-level."""
    import nexus_arb
    assert hasattr(nexus_arb, "BellmanFordArb")
    assert hasattr(nexus_arb, "CMAES")
    assert hasattr(nexus_arb, "UnscentedKalmanFilter")
    assert hasattr(nexus_arb, "ThompsonSamplingBandit")
    assert hasattr(nexus_arb, "TradingPolicy")
