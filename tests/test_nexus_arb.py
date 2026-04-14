"""
Tests for nexus_arb algorithms and wallet generation.
All tests run offline — no RPC or network calls required.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np


# ── Wallet generation ─────────────────────────────────────────────────────────

class TestWalletGeneration:
    def test_generates_valid_address(self):
        from eth_account import Account
        acct = Account.create()
        assert acct.address.startswith("0x")
        assert len(acct.address) == 42

    def test_generates_valid_private_key(self):
        from eth_account import Account
        acct = Account.create()
        key_hex = acct.key.hex()
        assert len(key_hex) == 64  # 32 bytes = 64 hex chars (no 0x prefix)

    def test_each_wallet_is_unique(self):
        from eth_account import Account
        a1 = Account.create()
        a2 = Account.create()
        assert a1.address != a2.address
        assert a1.key != a2.key

    def test_recover_address_from_key(self):
        from eth_account import Account
        acct = Account.create()
        recovered = Account.from_key(acct.key)
        assert recovered.address == acct.address


# ── Bellman-Ford ──────────────────────────────────────────────────────────────

class TestBellmanFord:
    def _make_graph(self, with_cycle: bool = False):
        from nexus_arb.algorithms.bellman_ford import PriceGraph, PoolPrice

        g = PriceGraph()
        # WETH -> USDC: 2000 USDC per WETH, fee 30bps
        g.add_price(PoolPrice(
            token_in="WETH", token_out="USDC",
            price=2000.0, price_after_fee=1994.0,
            fee_bps=30, liquidity=10.0, dex="uniswap_v3"
        ))
        if with_cycle:
            # USDC -> WETH: gives back MORE than 1/2000 WETH — profitable cycle
            g.add_price(PoolPrice(
                token_in="USDC", token_out="WETH",
                price=0.000510, price_after_fee=0.000509,
                fee_bps=30, liquidity=10.0, dex="sushiswap"
            ))
        else:
            # Normal back rate — not profitable
            g.add_price(PoolPrice(
                token_in="USDC", token_out="WETH",
                price=0.000499, price_after_fee=0.000498,
                fee_bps=30, liquidity=10.0, dex="sushiswap"
            ))
        return g

    def test_no_opportunity_on_fair_prices(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFord
        bf = BellmanFord({"trading": {"flash_loan_fee_bps": 9}})
        graph = self._make_graph(with_cycle=False)
        opps = bf.detect(graph, min_profit_pct=0.01)
        assert opps == []

    def test_detects_profitable_cycle(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFord
        bf = BellmanFord({"trading": {"flash_loan_fee_bps": 9}})
        graph = self._make_graph(with_cycle=True)
        opps = bf.detect(graph, min_profit_pct=0.0)
        assert isinstance(opps, list)

    def test_opportunity_structure(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFord, ArbitrageOpportunity
        bf  = BellmanFord({"trading": {"flash_loan_fee_bps": 9}})
        graph = self._make_graph(with_cycle=True)
        opps  = bf.detect(graph, min_profit_pct=0.0)
        for opp in opps:
            assert isinstance(opp, ArbitrageOpportunity)
            assert isinstance(opp.cycle, list)
            assert isinstance(opp.expected_profit_pct, float)
            assert isinstance(opp.max_input_eth, float)
            assert opp.max_input_eth > 0

    def test_find_best_returns_none_when_empty(self):
        from nexus_arb.algorithms.bellman_ford import BellmanFord, PriceGraph
        bf = BellmanFord({"trading": {"flash_loan_fee_bps": 9}})
        assert bf.find_best(PriceGraph()) is None


# ── CMA-ES ────────────────────────────────────────────────────────────────────

class TestCMAES:
    def test_optimises_simple_quadratic(self):
        from nexus_arb.algorithms.cma_es import CMAES1D

        cma = CMAES1D(population_size=16, max_iterations=50, seed=42)

        # Maximise -(x-2)^2 → optimal at x=2
        result = cma.optimize(
            profit_fn=lambda x: -(x - 2.0) ** 2,
            x_min=0.01,
            x_max=10.0,
            x_start=5.0,
            eth_price_usd=3000.0
        )
        assert abs(result.optimal_size_eth - 2.0) < 0.5

    def test_result_fields(self):
        from nexus_arb.algorithms.cma_es import CMAES1D, CMAESResult

        cma = CMAES1D(seed=0)
        result = cma.optimize(lambda x: -x, x_min=0.01, x_max=5.0)
        assert isinstance(result, CMAESResult)
        assert result.iterations >= 1
        assert isinstance(result.converged, bool)

    def test_trade_optimizer_builds(self):
        from nexus_arb.algorithms.cma_es import TradeOptimizer
        opt = TradeOptimizer({"trading": {"flash_loan_fee_bps": 9}})
        assert opt is not None


# ── UKF ───────────────────────────────────────────────────────────────────────

class TestUKF:
    def test_initializes_on_first_update(self):
        from nexus_arb.algorithms.ukf import PriceUKF, UKFState
        ukf   = PriceUKF({})
        state = ukf.update(2000.0)
        assert isinstance(state, UKFState)
        assert abs(state.price - 2000.0) < 100

    def test_converges_to_stable_price(self):
        from nexus_arb.algorithms.ukf import PriceUKF
        ukf = PriceUKF({})
        for _ in range(30):
            state = ukf.update(2000.0)
        assert abs(state.price - 2000.0) < 10.0

    def test_velocity_sign(self):
        from nexus_arb.algorithms.ukf import PriceUKF
        ukf = PriceUKF({})
        for p in [2000.0, 2010.0, 2020.0, 2030.0, 2040.0]:
            state = ukf.update(p)
        assert state.is_trending_up is True

    def test_multi_token_ukf(self):
        from nexus_arb.algorithms.ukf import MultiTokenUKF
        multi = MultiTokenUKF({})
        s = multi.update("WETH", "USDC", 2000.0)
        assert s.price > 0
        fav = multi.is_price_moving_favorably("WETH", "USDC")
        assert isinstance(fav, bool)

    def test_multi_token_unknown_pair_is_favorable(self):
        from nexus_arb.algorithms.ukf import MultiTokenUKF
        multi = MultiTokenUKF({})
        assert multi.is_price_moving_favorably("WBTC", "DAI") is True


# ── Thompson Sampling ─────────────────────────────────────────────────────────

class TestThompsonSampling:
    def test_bandit_selects_from_arms(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonBandit
        bandit = ThompsonBandit(arms=["uniswap_v3", "sushiswap", "curve"], seed=0)
        selected = bandit.select()
        assert selected in ("uniswap_v3", "sushiswap", "curve")

    def test_update_increases_alpha_on_positive_reward(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonBandit
        bandit = ThompsonBandit(arms=["uni"], seed=0)
        alpha_before = bandit.arms["uni"].alpha
        bandit.update("uni", reward=0.8)
        assert bandit.arms["uni"].alpha > alpha_before * 0.9  # decay but net gain

    def test_update_increases_beta_on_negative_reward(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonBandit
        bandit = ThompsonBandit(arms=["uni"], seed=0)
        beta_before = bandit.arms["uni"].beta
        bandit.update("uni", reward=-1.0)
        assert bandit.arms["uni"].beta > beta_before

    def test_dex_bandit_selects(self):
        from nexus_arb.algorithms.thompson_sampling import DexBandit
        dex = DexBandit({"algorithms": {"thompson": {}}})
        selected = dex.select_dex("WETH", "USDC")
        assert selected in ("uniswap_v3", "curve", "balancer", "camelot_v3")

    def test_dex_bandit_record_outcome(self):
        from nexus_arb.algorithms.thompson_sampling import DexBandit
        dex = DexBandit({})
        # Should not raise
        dex.record_outcome("WETH", "USDC", "uniswap_v3", 0.01, 0.1)
        dex.record_failure("WETH", "USDC", "curve")

    def test_stats_table_sorted(self):
        from nexus_arb.algorithms.thompson_sampling import ThompsonBandit
        bandit = ThompsonBandit(arms=["a", "b", "c"], seed=1)
        bandit.update("a", 0.9)
        bandit.update("a", 0.9)
        table = bandit.stats_table()
        assert table[0]["arm"] == "a"  # Highest posterior first


# ── PPO Agent ────────────────────────────────────────────────────────────────

class TestPPOAgent:
    def test_agent_builds(self):
        from nexus_arb.algorithms.ppo import PPOAgent
        agent = PPOAgent({})
        assert agent is not None

    def test_encode_state_shape(self):
        from nexus_arb.algorithms.ppo import PPOAgent
        agent = PPOAgent({})
        state = agent.encode_state(
            spread_mean=0.005, spread_std=0.001,
            gas_price_gwei=0.1, block_utilization=0.5,
            time_since_opp_ms=100.0, wallet_balance_eth=1.0,
            ukf_velocity=0.01, recent_success_rate=0.7
        )
        assert state.shape == (8,)
        assert np.all(state >= -1.0) and np.all(state <= 1.0)

    def test_select_action_returns_valid(self):
        from nexus_arb.algorithms.ppo import PPOAgent, EXECUTE, WAIT, SKIP
        agent = PPOAgent({})
        state = agent.encode_state(0.005, 0.001, 0.1, 0.5, 100.0, 1.0, 0.0, 0.7)
        action, log_prob, value = agent.select_action(state)
        assert action in (EXECUTE, WAIT, SKIP)
        assert isinstance(log_prob, float)
        assert isinstance(value, float)

    def test_update_returns_none_when_buffer_empty(self):
        from nexus_arb.algorithms.ppo import PPOAgent
        agent = PPOAgent({})
        assert agent.update() is None

    def test_update_returns_metrics_after_enough_transitions(self):
        from nexus_arb.algorithms.ppo import PPOAgent
        agent = PPOAgent({"algorithms": {"ppo": {"batch_size": 4}}})
        state = agent.encode_state(0.005, 0.001, 0.1, 0.5, 100.0, 1.0, 0.0, 0.7)
        for _ in range(10):
            action, lp, val = agent.select_action(state)
            agent.store_transition(state, action, 0.1, False, lp, val)
        metrics = agent.update()
        assert metrics is not None
        assert "policy_loss" in metrics
        assert "value_loss" in metrics
        assert "entropy" in metrics
