"""
Advanced tests for untested code paths across AUREON modules.

Covers:
  - intelligence/autonomy.py  (AgentLoop init, _build_engine)
  - nexus_arb/algorithms      (BellmanFord, CMAES, TradingPolicy, Thompson, UKF)
  - engine/portfolio.py       (save_trade_log, PnL after buy+sell)
  - config.py                 (BotConfig attributes)
"""

import json
import os
import sys

import numpy as np
import pytest

# ── Ensure project root is importable ─────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTONOMY MODULE
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentLoopInit:
    """AgentLoop.__init__ sets expected defaults."""

    def test_running_is_false(self):
        from intelligence.autonomy import AgentLoop

        agent = AgentLoop()
        assert agent.running is False

    def test_cycle_count_is_zero(self):
        from intelligence.autonomy import AgentLoop

        agent = AgentLoop()
        assert agent.cycle_count == 0

    def test_supervisor_is_none(self):
        from intelligence.autonomy import AgentLoop

        agent = AgentLoop()
        assert agent._supervisor is None

    def test_price_engine_is_none(self):
        from intelligence.autonomy import AgentLoop

        agent = AgentLoop()
        assert agent._price_engine is None


class TestBuildEngine:
    """AgentLoop._build_engine returns dict with expected keys."""

    def test_build_engine_returns_dict(self):
        from intelligence.autonomy import AgentLoop

        agent = AgentLoop()
        eng = agent._build_engine()
        assert isinstance(eng, dict)

    def test_build_engine_has_expected_keys(self):
        from intelligence.autonomy import AgentLoop

        agent = AgentLoop()
        eng = agent._build_engine()
        expected_keys = {"market", "portfolio", "risk", "strategy", "arb", "liquidity", "executor"}
        assert set(eng.keys()) == expected_keys

    def test_build_engine_values_not_none(self):
        from intelligence.autonomy import AgentLoop

        agent = AgentLoop()
        eng = agent._build_engine()
        for key, val in eng.items():
            assert val is not None, f"Engine key {key!r} is None"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BELLMAN-FORD ARBITRAGE
# ═══════════════════════════════════════════════════════════════════════════════


class TestBellmanFordArb:
    """Edge-case tests for BellmanFordArb."""

    def _make_arb(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFordArb
        return BellmanFordArb()

    def test_empty_graph_returns_no_cycle(self):
        arb = self._make_arb()
        result = arb.find_arbitrage()
        assert result.has_cycle is False
        assert result.cycle == []
        assert result.profit_ratio == 1.0

    def test_single_edge_no_cycle(self):
        arb = self._make_arb()
        arb.add_edge("WETH", "USDC", 2000.0, "uni")
        result = arb.find_arbitrage("WETH")
        assert result.has_cycle is False

    def test_no_profit_cycle(self):
        """A round-trip with rates that multiply to < 1 should not flag as profitable."""
        arb = self._make_arb()
        arb.add_edge("WETH", "USDC", 2000.0, "uni")
        arb.add_edge("USDC", "WETH", 0.0004, "sushi")  # 2000*0.0004 = 0.8 < 1
        result = arb.find_arbitrage("WETH")
        assert result.has_cycle is False

    def test_profitable_cycle_detected(self):
        arb = self._make_arb()
        arb.add_edge("WETH", "USDC", 2000.0, "uni")
        arb.add_edge("USDC", "WETH", 0.0006, "sushi")  # 2000*0.0006 = 1.2 > 1
        result = arb.find_arbitrage("WETH")
        assert result.has_cycle is True
        assert result.profit_ratio > 1.0

    def test_clear_empties_graph(self):
        arb = self._make_arb()
        arb.add_edge("A", "B", 1.5, "dex")
        arb.clear()
        assert arb._edges == []
        assert len(arb._nodes) == 0
        result = arb.find_arbitrage()
        assert result.has_cycle is False

    def test_find_best_arbitrage_empty(self):
        arb = self._make_arb()
        result = arb.find_best_arbitrage()
        assert result.has_cycle is False
        assert result.cycle == []

    def test_add_price_matrix(self):
        arb = self._make_arb()
        prices = {
            "WETH": {"USDC": 2000.0, "DAI": 1999.5},
            "USDC": {"WETH": 0.0005, "DAI": 0.999},
            "DAI":  {"USDC": 1.001, "WETH": 0.0005002},
        }
        arb.add_price_matrix(prices, dex="matrix")
        assert len(arb._edges) == 6
        assert "WETH" in arb._nodes
        assert "USDC" in arb._nodes
        assert "DAI" in arb._nodes

    def test_add_edge_zero_rate_raises(self):
        arb = self._make_arb()
        with pytest.raises(ValueError, match="positive"):
            arb.add_edge("A", "B", 0.0, "dex")

    def test_add_edge_negative_rate_raises(self):
        arb = self._make_arb()
        with pytest.raises(ValueError, match="positive"):
            arb.add_edge("A", "B", -1.0, "dex")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CMA-ES OPTIMIZER
# ═══════════════════════════════════════════════════════════════════════════════


class TestCMAES:
    """Tests for the CMA-ES optimizer."""

    def test_minimize_quadratic(self):
        """Minimise f(x)=||x||^2; optimum is near zero."""
        from nexus_arb.algorithms.cma_es import CMAES

        opt = CMAES(n_dim=2, sigma0=0.5, seed=42)
        result = opt.minimize(lambda x: float(np.sum(x ** 2)), np.array([3.0, 4.0]))
        assert result.f_opt < 0.1
        assert np.linalg.norm(result.x_opt) < 1.0

    def test_sigma0_from_x0_minimum(self):
        """sigma0_from_x0 returns at least 0.01."""
        from nexus_arb.algorithms.cma_es import CMAES

        assert CMAES.sigma0_from_x0(np.zeros(5)) >= 0.01

    def test_sigma0_from_x0_scales_with_norm(self):
        from nexus_arb.algorithms.cma_es import CMAES

        x0 = np.array([10.0, 10.0])
        s = CMAES.sigma0_from_x0(x0, scale=0.3)
        expected = np.linalg.norm(x0) * 0.3
        assert abs(s - expected) < 1e-6

    def test_position_sizing_objective_returns_callable(self):
        from nexus_arb.algorithms.cma_es import CMAES

        returns = np.random.default_rng(0).normal(0, 0.01, (50, 3))
        gas = np.array([0.001, 0.002, 0.001])
        obj = CMAES.position_sizing_objective(returns, gas, max_exposure=1.0)
        val = obj(np.array([0.3, 0.3, 0.3]))
        assert isinstance(val, float)

    def test_invalid_n_dim_raises(self):
        from nexus_arb.algorithms.cma_es import CMAES

        with pytest.raises(ValueError, match="n_dim"):
            CMAES(n_dim=0)

    def test_invalid_sigma0_raises(self):
        from nexus_arb.algorithms.cma_es import CMAES

        with pytest.raises(ValueError, match="sigma0"):
            CMAES(n_dim=2, sigma0=-1.0)

    def test_optim_result_has_history(self):
        from nexus_arb.algorithms.cma_es import CMAES

        opt = CMAES(n_dim=1, sigma0=0.5, seed=7)
        result = opt.minimize(lambda x: float(x[0] ** 2), np.array([1.0]), n_generations=5)
        assert len(result.history) == 5
        assert result.n_evals > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PPO TRADING POLICY
# ═══════════════════════════════════════════════════════════════════════════════


class TestTradingPolicy:
    """Tests for TradingPolicy (PPO)."""

    def _make_state(self):
        return np.array([0.5, 0.001, 0.02, 0.0, 0.0, 1.0], dtype=np.float64)

    def _make_policy(self):
        from nexus_arb.algorithms.ppo import TradingPolicy
        return TradingPolicy(seed=42)

    def test_select_action_returns_valid_action(self):
        policy = self._make_policy()
        action, log_prob, value = policy.select_action(self._make_state())
        assert action in {0, 1, 2}
        assert isinstance(log_prob, float)
        assert isinstance(value, float)

    def test_update_empty_transitions_returns_zeros(self):
        policy = self._make_policy()
        result = policy.update([], last_value=0.0)
        assert result.policy_loss == 0.0
        assert result.value_loss == 0.0
        assert result.entropy == 0.0
        assert result.n_updates == 0

    def test_action_name_mapping(self):
        policy = self._make_policy()
        assert policy.action_name(0) == "HOLD"
        assert policy.action_name(1) == "BUY"
        assert policy.action_name(2) == "SELL"
        assert policy.action_name(99) == "HOLD"  # unknown defaults to HOLD

    def test_value_returns_float(self):
        policy = self._make_policy()
        v = policy.value(self._make_state())
        assert isinstance(v, float)

    def test_policy_probs_sums_to_one(self):
        policy = self._make_policy()
        probs = policy.policy_probs(self._make_state())
        assert probs.shape == (3,)
        assert abs(probs.sum() - 1.0) < 1e-6
        assert all(p >= 0 for p in probs)

    def test_encode_state_shape(self):
        from nexus_arb.algorithms.ppo import TradingPolicy

        state = TradingPolicy.encode_state(
            price=2500.0, prev_price=2490.0,
            volatility=0.02, position=0.5,
            drawdown=0.01, cash_ratio=0.8,
        )
        assert state.shape == (6,)
        assert state.dtype == np.float64

    def test_stats_returns_expected_keys(self):
        policy = self._make_policy()
        s = policy.stats()
        assert isinstance(s, dict)
        for key in ("total_updates", "n_actions", "state_dim", "clip_eps", "lr", "gamma", "gae_lambda"):
            assert key in s, f"Missing key {key!r}"

    def test_update_with_transitions(self):
        from nexus_arb.algorithms.ppo import Transition

        policy = self._make_policy()
        state = self._make_state()
        transitions = []
        for _ in range(5):
            a, lp, v = policy.select_action(state)
            transitions.append(Transition(state.copy(), a, 0.01, lp, v, False))
        result = policy.update(transitions, last_value=0.0)
        assert isinstance(result.policy_loss, float)
        assert result.n_updates > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 5. THOMPSON SAMPLING BANDIT
# ═══════════════════════════════════════════════════════════════════════════════


class TestThompsonSamplingBandit:
    """Tests for ThompsonSamplingBandit."""

    def _make_bandit(self, arms=None, seed=42):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
        return ThompsonSamplingBandit(arms or ["uni", "sushi", "curve"], seed=seed)

    def test_select_top_k_returns_correct_count(self):
        bandit = self._make_bandit()
        top2 = bandit.select_top_k(2)
        assert len(top2) == 2
        assert all(a in ["uni", "sushi", "curve"] for a in top2)

    def test_select_top_k_clamped(self):
        bandit = self._make_bandit()
        top10 = bandit.select_top_k(10)
        assert len(top10) == 3  # only 3 arms

    def test_reset_clears_to_prior(self):
        bandit = self._make_bandit()
        bandit.update("uni", 0.9)
        bandit.reset()
        stats = bandit.stats()
        for arm_stats in stats.values():
            assert arm_stats["alpha"] == 1.0
            assert arm_stats["beta"] == 1.0
            assert arm_stats["n_selected"] == 0

    def test_confidence_interval_returns_tuple_of_2_floats(self):
        bandit = self._make_bandit()
        lo, hi = bandit.confidence_interval("uni")
        assert isinstance(lo, float)
        assert isinstance(hi, float)
        assert 0.0 <= lo <= hi <= 1.0

    def test_expected_rewards_returns_all_arms(self):
        bandit = self._make_bandit()
        rewards = bandit.expected_rewards()
        assert set(rewards.keys()) == {"uni", "sushi", "curve"}
        for v in rewards.values():
            assert 0.0 < v < 1.0

    def test_update_unknown_arm_raises(self):
        bandit = self._make_bandit()
        with pytest.raises(ValueError, match="Unknown arm"):
            bandit.update("balancer", 0.5)

    def test_empty_arms_raises(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit

        with pytest.raises(ValueError, match="At least one arm"):
            ThompsonSamplingBandit([])

    def test_select_returns_valid_arm(self):
        bandit = self._make_bandit()
        arm = bandit.select()
        assert arm in ["uni", "sushi", "curve"]

    def test_best_arm_after_updates(self):
        bandit = self._make_bandit()
        for _ in range(20):
            bandit.update("uni", 0.95)
            bandit.update("sushi", 0.1)
            bandit.update("curve", 0.1)
        assert bandit.best_arm() == "uni"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. UNSCENTED KALMAN FILTER
# ═══════════════════════════════════════════════════════════════════════════════


class TestUKF:
    """Tests for UnscentedKalmanFilter."""

    def _make_ukf(self):
        from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
        return UnscentedKalmanFilter()

    def test_initialize_sets_state(self):
        ukf = self._make_ukf()
        ukf.initialize(2000.0)
        s = ukf.state
        assert s is not None
        assert abs(s[0] - 2000.0) < 1e-6
        assert abs(s[1] - 0.0) < 1e-6

    def test_predict_ahead_raises_if_not_initialized(self):
        ukf = self._make_ukf()
        with pytest.raises(RuntimeError, match="not initialized"):
            ukf.predict_ahead(steps=1)

    def test_predict_ahead_returns_array(self):
        ukf = self._make_ukf()
        ukf.initialize(2000.0, velocity=1.0)
        pred = ukf.predict_ahead(steps=3)
        assert isinstance(pred, np.ndarray)
        assert pred.shape == (2,)
        assert pred[0] > 2000.0  # price should increase with positive velocity

    def test_state_property_none_before_init(self):
        ukf = self._make_ukf()
        assert ukf.state is None

    def test_covariance_property_none_before_init(self):
        ukf = self._make_ukf()
        assert ukf.covariance is None

    def test_multiple_sequential_updates(self):
        ukf = self._make_ukf()
        prices = [2000.0, 2005.0, 2003.0, 2010.0, 2008.0]
        for p in prices:
            result = ukf.update(p)
            assert result.mean is not None
            assert result.covariance is not None
        s = ukf.state
        assert s is not None
        assert s[0] > 0

    def test_price_validation_negative_raises(self):
        ukf = self._make_ukf()
        with pytest.raises(ValueError, match="positive"):
            ukf.update(-100.0)

    def test_price_validation_zero_raises(self):
        ukf = self._make_ukf()
        with pytest.raises(ValueError, match="positive"):
            ukf.update(0.0)

    def test_anomaly_detection(self):
        ukf = self._make_ukf()
        # Feed steady prices then a huge jump
        for _ in range(10):
            ukf.update(2000.0)
        result = ukf.update(5000.0)  # massive jump
        assert result.is_anomaly is True

    def test_auto_initialize_on_first_update(self):
        ukf = self._make_ukf()
        result = ukf.update(1500.0)
        assert ukf.state is not None
        assert abs(ukf.state[0] - 1500.0) < 1e-6
        assert result.innovation == 0.0  # first obs has zero innovation

    def test_covariance_after_init(self):
        ukf = self._make_ukf()
        ukf.initialize(2000.0)
        cov = ukf.covariance
        assert cov is not None
        assert cov.shape == (2, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. PORTFOLIO EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════


class TestPortfolioAdvanced:
    """save_trade_log and PnL verification."""

    def test_save_trade_log_creates_valid_json(self, tmp_path):
        from engine.portfolio import Portfolio

        p = Portfolio(initial_usd=5000.0)
        p.log_trade("BUY", 2000.0, 1.0)
        log_path = str(tmp_path / "trade_log.json")
        p.save_trade_log(log_path)

        assert os.path.isfile(log_path)
        with open(log_path) as f:
            data = json.load(f)
        assert "saved_at" in data
        assert "initial_usd" in data
        assert "summary" in data
        assert "trades" in data
        assert len(data["trades"]) == 1

    def test_pnl_after_buy_sell_cycle(self):
        """Buy low, sell high → positive PnL."""
        from engine.portfolio import Portfolio

        p = Portfolio(initial_usd=10000.0)
        p.log_trade("BUY", 2000.0, 1.0)   # spend 2000 to buy 1 ETH
        p.log_trade("SELL", 2500.0, 1.0)   # sell 1 ETH for 2500

        s = p.summary()
        assert s["pnl_usd"] == 500.0
        assert s["balance_eth"] == 0.0
        assert s["balance_usd"] == 10500.0

    def test_pnl_negative_after_buy_sell_loss(self):
        """Buy high, sell low → negative PnL."""
        from engine.portfolio import Portfolio

        p = Portfolio(initial_usd=10000.0)
        p.log_trade("BUY", 2500.0, 1.0)
        p.log_trade("SELL", 2000.0, 1.0)

        s = p.summary()
        assert s["pnl_usd"] == -500.0

    def test_save_trade_log_nested_directory(self, tmp_path):
        """save_trade_log creates parent directories."""
        from engine.portfolio import Portfolio

        p = Portfolio()
        nested = str(tmp_path / "sub" / "dir" / "log.json")
        p.save_trade_log(nested)
        assert os.path.isfile(nested)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. CONFIG
# ═══════════════════════════════════════════════════════════════════════════════


class TestBotConfig:
    """Verify _Config / cfg has expected attributes."""

    def test_config_instantiates(self):
        from config import _Config

        c = _Config()
        assert c is not None

    def test_config_has_trading_attributes(self):
        from config import cfg

        assert hasattr(cfg, "TRADE_SIZE_ETH")
        assert hasattr(cfg, "SCAN_INTERVAL")
        assert hasattr(cfg, "MIN_PROFIT_USD")
        assert hasattr(cfg, "MAX_DAILY_TRADES")
        assert hasattr(cfg, "INITIAL_USD")

    def test_config_has_blockchain_attributes(self):
        from config import cfg

        assert hasattr(cfg, "RPC_URL")
        assert hasattr(cfg, "CHAIN_ID")
        assert hasattr(cfg, "MAX_GAS_LIMIT")

    def test_config_defaults(self):
        from config import _Config

        c = _Config()
        assert isinstance(c.CHAIN_ID, int)
        assert isinstance(c.TRADE_SIZE_ETH, float)
        assert isinstance(c.SCAN_INTERVAL, int)
        assert isinstance(c.DEBUG, bool)

    def test_is_live_ready_without_env(self, monkeypatch):
        from config import _Config

        c = _Config()
        monkeypatch.setattr(c, "RPC_URL", None)
        monkeypatch.setattr(c, "PRIVATE_KEY", None)
        monkeypatch.setattr(c, "WALLET_ADDRESS", None)
        monkeypatch.setattr(c, "ALCHEMY_API_KEY", None)
        assert c.is_live_ready() is False

    def test_get_rpc_url_returns_none_without_env(self, monkeypatch):
        from config import _Config

        c = _Config()
        monkeypatch.setattr(c, "RPC_URL", None)
        monkeypatch.setattr(c, "ALCHEMY_API_KEY", None)
        assert c.get_rpc_url() is None
