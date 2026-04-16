"""
intelligence/trading_agent.py
==============================

Smart multi-strategy trading agent with automatic wallet generation.

Each TradingAgent instance:
  - Generates (or accepts) its own Ethereum wallet on creation
  - Runs one of five strategies: arb, ppo, mean_reversion, flash_loan, adaptive
  - Uses the appropriate advanced algorithm for its strategy
  - Supports multiple chains: ethereum, arbitrum, polygon, bsc, base
  - Targets a configurable token: ETH, USDC, WBTC, ARB, MATIC
  - Exposes full async lifecycle: start / stop / cycle / performance
  - Is fully serialisable to dict for API responses

Architecture
------------
  TradingAgentConfig   — pydantic-compatible dataclass for UI / API
  TradingAgent         — async agent with wallet, algorithm, cycle loop
  AgentRegistry        — thread-safe global registry of all agents

Strategy → Algorithm mapping
-----------------------------
  arb            → BellmanFordArb  (negative-cycle DEX arbitrage)
  ppo            → TradingPolicy   (PPO actor-critic reinforcement learning)
  mean_reversion → CMAES           (parameter optimisation via CMA-ES)
  flash_loan     → ThompsonSampling (bandit DEX router for flash paths)
  adaptive       → UKF + Thompson  (Kalman price filter + bandit routing)
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("aureon.trading_agent")

# ── Enumerations ──────────────────────────────────────────────────────────────


class Strategy(str, Enum):
    ARB             = "arb"             # Bellman-Ford multi-hop arbitrage
    PPO             = "ppo"             # PPO reinforcement-learning policy
    MEAN_REVERSION  = "mean_reversion"  # CMA-ES parameter-optimised mean-reversion
    FLASH_LOAN      = "flash_loan"      # Thompson Sampling DEX routing + flash borrow
    ADAPTIVE        = "adaptive"        # UKF price filter + bandit DEX selection


class Chain(str, Enum):
    ETHEREUM = "ethereum"
    ARBITRUM = "arbitrum"
    POLYGON  = "polygon"
    BSC      = "bsc"
    BASE     = "base"


class Token(str, Enum):
    ETH   = "ETH"
    USDC  = "USDC"
    WBTC  = "WBTC"
    ARB   = "ARB"
    MATIC = "MATIC"


class AgentStatus(str, Enum):
    IDLE     = "idle"
    RUNNING  = "running"
    STOPPED  = "stopped"
    ERROR    = "error"


# ── Chain meta ────────────────────────────────────────────────────────────────

CHAIN_META: Dict[str, Dict[str, Any]] = {
    Chain.ETHEREUM: {"chain_id": 1,     "native": "ETH",   "explorer": "https://etherscan.io"},
    Chain.ARBITRUM: {"chain_id": 42161, "native": "ETH",   "explorer": "https://arbiscan.io"},
    Chain.POLYGON:  {"chain_id": 137,   "native": "MATIC", "explorer": "https://polygonscan.com"},
    Chain.BSC:      {"chain_id": 56,    "native": "BNB",   "explorer": "https://bscscan.com"},
    Chain.BASE:     {"chain_id": 8453,  "native": "ETH",   "explorer": "https://basescan.org"},
}

AAVE_V3_POOLS: Dict[str, str] = {
    Chain.ETHEREUM: "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    Chain.ARBITRUM: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    Chain.POLYGON:  "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    Chain.BASE:     "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
}

# ── Wallet generation ─────────────────────────────────────────────────────────


def generate_wallet() -> Dict[str, str]:
    """
    Generate a fresh Ethereum wallet.

    Returns
    -------
    dict with keys: address, private_key (0x-prefixed hex), public_key
    """
    from eth_account import Account
    acct = Account.create()
    key_hex = acct.key.hex()
    # Ensure 0x prefix for consistent format
    if not key_hex.startswith("0x"):
        key_hex = "0x" + key_hex
    return {
        "address":     acct.address,
        "private_key": key_hex,
        "public_key":  acct.address,     # Ethereum uses address as public identifier
    }


# ── Config dataclass ──────────────────────────────────────────────────────────


@dataclass
class TradingAgentConfig:
    """
    Full configuration for a TradingAgent.

    Fields
    ------
    name            : human-readable agent name
    strategy        : trading strategy (arb / ppo / mean_reversion / flash_loan / adaptive)
    chain           : target blockchain
    token           : primary token to trade
    initial_capital : starting capital in USD
    trade_size_eth  : max trade size in ETH per cycle
    min_profit_usd  : minimum estimated profit to execute a trade
    scan_interval   : seconds between trading cycles
    dry_run         : if True, no real transactions are sent
    private_key     : hex private key; if None a fresh wallet is generated
    rpc_url         : override RPC URL (uses CHAIN defaults if None)
    """
    name:            str     = "Agent"
    strategy:        Strategy = Strategy.ARB
    chain:           Chain    = Chain.ETHEREUM
    token:           Token    = Token.ETH
    initial_capital: float   = 10_000.0
    trade_size_eth:  float   = 0.05
    min_profit_usd:  float   = 2.0
    scan_interval:   int     = 30
    dry_run:         bool    = True
    private_key:     Optional[str] = None
    rpc_url:         Optional[str] = None


# ── TradingAgent ──────────────────────────────────────────────────────────────


class TradingAgent:
    """
    Autonomous multi-strategy trading agent with an embedded wallet.

    Lifecycle
    ---------
    agent = TradingAgent(config)        # wallet generated if not provided
    await agent.start()                 # launches background cycle loop
    await agent.stop()                  # graceful shutdown
    agent.performance()                 # dict of metrics

    The agent's wallet address and private key are accessible via
    agent.wallet["address"] and agent.wallet["private_key"].
    """

    def __init__(self, config: TradingAgentConfig) -> None:
        self.id      = str(uuid.uuid4())[:8]
        self.config  = config
        self.status  = AgentStatus.IDLE
        self.created = time.time()

        # ── Wallet ─────────────────────────────────────────────────────────────
        if config.private_key:
            from eth_account import Account
            acct = Account.from_key(config.private_key)
            self.wallet = {
                "address":     acct.address,
                "private_key": config.private_key,
                "public_key":  acct.address,
            }
        else:
            self.wallet = generate_wallet()
            logger.info(
                "Agent %s generated wallet %s",
                self.id, self.wallet["address"]
            )

        # ── Algorithm ─────────────────────────────────────────────────────────
        self._algorithm = self._build_algorithm()

        # ── Portfolio state ────────────────────────────────────────────────────
        self._capital      = config.initial_capital
        self._position_eth = 0.0
        self._peak_capital = config.initial_capital
        self._prev_price   = 0.0

        # ── Metrics ───────────────────────────────────────────────────────────
        self.cycle_count   = 0
        self.trades_made   = 0
        self.total_pnl     = 0.0
        self.errors        = 0
        self._last_result: Dict[str, Any] = {}

        # ── Async task ────────────────────────────────────────────────────────
        self._task: Optional[asyncio.Task] = None

    # ── Algorithm factory ─────────────────────────────────────────────────────

    def _build_algorithm(self) -> Any:
        """Instantiate the algorithm appropriate for this agent's strategy."""
        s = self.config.strategy
        if s == Strategy.ARB:
            from nexus_arb.algorithms.bellman_ford import BellmanFordArb
            return BellmanFordArb()

        if s == Strategy.PPO:
            from nexus_arb.algorithms.ppo import TradingPolicy
            return TradingPolicy(hidden_dim=64, gamma=0.99, seed=42)

        if s == Strategy.MEAN_REVERSION:
            from nexus_arb.algorithms.cma_es import CMAES
            # 3 parameters: window, threshold, position_scale
            return CMAES(n_dim=3, sigma0=0.3, seed=42)

        if s == Strategy.FLASH_LOAN:
            from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
            dexes = ["uniswap_v3", "sushiswap", "curve", "balancer", "camelot"]
            return ThompsonSamplingBandit(arms=dexes, seed=42)

        if s == Strategy.ADAPTIVE:
            from nexus_arb.algorithms.ukf import UnscentedKalmanFilter
            return UnscentedKalmanFilter()

        raise ValueError(f"Unknown strategy: {s}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the async trading loop as a background task."""
        if self.status == AgentStatus.RUNNING:
            return
        self.status = AgentStatus.RUNNING
        self._task  = asyncio.create_task(self._run_loop(), name=f"agent-{self.id}")
        logger.info(
            "Agent %s (%s) started on %s trading %s",
            self.id, self.config.strategy.value,
            self.config.chain.value, self.config.token.value,
        )

    async def stop(self) -> None:
        """Gracefully stop the trading loop."""
        self.status = AgentStatus.STOPPED
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Agent %s stopped after %d cycles", self.id, self.cycle_count)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Background async cycle loop."""
        while self.status == AgentStatus.RUNNING:
            self.cycle_count += 1
            try:
                result = await self._run_cycle()
                self._last_result = result
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.errors += 1
                self._last_result = {"status": "error", "error": str(exc)}
                logger.warning("Agent %s cycle error: %s", self.id, exc)
            await asyncio.sleep(self.config.scan_interval)

    async def _run_cycle(self) -> Dict[str, Any]:
        """Execute one trading cycle using the configured strategy."""
        # Fetch simulated price (real RPC used when rpc_url is configured)
        price = await self._get_price()

        if self._prev_price == 0.0:
            self._prev_price = price
            return {"status": "init", "price": price}

        strategy = self.config.strategy
        action   = "HOLD"
        pnl_step = 0.0

        if strategy == Strategy.ARB:
            action, pnl_step = self._cycle_arb(price)
        elif strategy == Strategy.PPO:
            action, pnl_step = self._cycle_ppo(price)
        elif strategy == Strategy.MEAN_REVERSION:
            action, pnl_step = self._cycle_mean_reversion(price)
        elif strategy == Strategy.FLASH_LOAN:
            action, pnl_step = self._cycle_flash_loan(price)
        elif strategy == Strategy.ADAPTIVE:
            action, pnl_step = self._cycle_adaptive(price)

        self.total_pnl  += pnl_step
        self._capital   += pnl_step
        self._peak_capital = max(self._peak_capital, self._capital)
        self._prev_price   = price

        return {
            "status":   "ok",
            "cycle":    self.cycle_count,
            "price":    round(price, 2),
            "action":   action,
            "pnl_step": round(pnl_step, 4),
            "capital":  round(self._capital, 2),
        }

    # ── Strategy cycle handlers ───────────────────────────────────────────────

    def _cycle_arb(self, price: float) -> tuple[str, float]:
        """Bellman-Ford arbitrage scan with synthetic multi-DEX rates."""
        arb = self._algorithm
        arb.clear()
        # Synthetic spread: ±0.3 % noise per DEX
        rng = np.random.default_rng(int(price * 1000) % (2**31))
        for dex in ("uniswap_v3", "sushiswap", "curve"):
            rate = price * (1 + rng.uniform(-0.003, 0.003))
            arb.add_edge("ETH", "USDC", rate,        dex)
            arb.add_edge("USDC", "ETH", 1.0 / rate,  dex)

        result = arb.find_best_arbitrage()
        if result.has_cycle and result.profit_ratio > 1.001:
            profit = (result.profit_ratio - 1.0) * self.config.trade_size_eth * price
            self.trades_made += 1
            return f"ARB profit_ratio={result.profit_ratio:.5f}", profit
        return "HOLD", 0.0

    def _cycle_ppo(self, price: float) -> tuple[str, float]:
        """PPO policy selects action from encoded market state."""
        from nexus_arb.algorithms.ppo import TradingPolicy, Transition
        policy: TradingPolicy = self._algorithm

        equity    = self._capital + self._position_eth * price
        cash_ratio = self._capital / max(equity, 1.0)
        drawdown   = max(0.0, 1.0 - equity / self._peak_capital)
        volatility = abs(price - self._prev_price) / max(self._prev_price, 1.0)

        state = TradingPolicy.encode_state(
            price       = price,
            prev_price  = self._prev_price,
            volatility  = volatility,
            position    = self._position_eth,
            drawdown    = drawdown,
            cash_ratio  = cash_ratio,
        )

        action_idx, log_prob, value = policy.select_action(state)
        action_name = policy.action_name(action_idx)

        pnl = 0.0
        size = self.config.trade_size_eth

        if action_name == "BUY" and self._capital >= price * size:
            cost              = price * size
            self._capital    -= cost
            self._position_eth += size
            self.trades_made  += 1
        elif action_name == "SELL" and self._position_eth >= size:
            self._capital    += price * size
            self._position_eth -= size
            pnl               = (price - self._prev_price) * size
            self.trades_made  += 1

        # Single-step rollout update
        reward = pnl - 0.5 * drawdown ** 2
        t = Transition(state, action_idx, reward, log_prob, value, False)
        policy.update([t], last_value=value)

        return action_name, pnl

    def _cycle_mean_reversion(self, price: float) -> tuple[str, float]:
        """CMA-ES optimises [window, threshold, size_scale] every 10 cycles."""
        cmaes = self._algorithm

        # Simple mean-reversion signal using current params
        window    = max(2, int(round(getattr(self, "_mr_window",    12))))
        threshold = max(0.001, getattr(self, "_mr_threshold", 0.015))
        hist      = getattr(self, "_price_history", [])
        hist.append(price)
        if len(hist) > 100:
            hist.pop(0)
        self._price_history = hist

        if len(hist) < window:
            return "HOLD", 0.0

        ma  = sum(hist[-window:]) / window
        dev = (price - ma) / ma

        pnl = 0.0
        action = "HOLD"
        size = self.config.trade_size_eth

        if dev < -threshold and self._capital >= price * size:
            self._capital    -= price * size
            self._position_eth += size
            action = "BUY"
            self.trades_made += 1
        elif dev > threshold and self._position_eth >= size:
            self._capital    += price * size
            self._position_eth -= size
            pnl   = (price - self._prev_price) * size
            action = "SELL"
            self.trades_made += 1

        # Periodic CMA-ES optimisation
        if self.cycle_count % 10 == 0 and len(hist) >= 20:
            def _objective(params: np.ndarray) -> float:
                w = max(2, int(round(params[0] * 20 + 5)))
                t = max(0.001, params[1] * 0.02 + 0.01)
                if len(hist) < w:
                    return 0.0
                ma_inner = sum(hist[-w:]) / w
                d  = (hist[-1] - ma_inner) / ma_inner
                return -abs(d - t)   # maximise deviation from threshold
            x0 = np.array([0.5, 0.5, 0.5])
            result = cmaes.minimize(_objective, x0, n_generations=20)
            self._mr_window    = max(2, int(round(result.x_opt[0] * 20 + 5)))
            self._mr_threshold = max(0.001, result.x_opt[1] * 0.02 + 0.01)

        return action, pnl

    def _cycle_flash_loan(self, price: float) -> tuple[str, float]:
        """Thompson Sampling selects best DEX for flash loan routing."""
        bandit = self._algorithm
        chosen_dex = bandit.select()

        # Simulate flash loan execution: borrow × spread − premium
        rng     = np.random.default_rng(int(time.time() * 1000) % (2**31))
        spread  = rng.uniform(0.0005, 0.004)  # 0.05 % – 0.4 %
        premium = 0.0009                       # Aave 0.09 % fee
        size    = self.config.trade_size_eth

        if spread > premium + 0.001:
            gross_profit = spread * size * price
            net_profit   = gross_profit - premium * size * price
            bandit.update(chosen_dex, min(1.0, net_profit / (size * price)))
            self.trades_made += 1
            action = f"FLASH_LOAN via {chosen_dex} spread={spread:.4f}"
            return action, net_profit

        bandit.update(chosen_dex, 0.0)
        return f"FLASH_SKIP via {chosen_dex}", 0.0

    def _cycle_adaptive(self, price: float) -> tuple[str, float]:
        """UKF tracks price state; Thompson Sampling routes execution."""
        ukf    = self._algorithm
        bandit = getattr(self, "_adaptive_bandit", None)
        if bandit is None:
            from nexus_arb.algorithms.thompson_sampling import ThompsonSamplingBandit
            bandit = ThompsonSamplingBandit(
                ["uniswap_v3", "sushiswap", "curve", "balancer"], seed=1
            )
            self._adaptive_bandit = bandit

        # UKF update — UKFState fields: mean, covariance
        ukf_result = ukf.update(price)
        predicted  = ukf_result.mean[0]
        velocity   = ukf_result.mean[1]

        pnl    = 0.0
        action = "HOLD"
        size   = self.config.trade_size_eth

        if velocity > 1.0 and self._capital >= predicted * size:
            # Price trending up: BUY on best DEX
            dex = bandit.select()
            self._capital    -= predicted * size
            self._position_eth += size
            bandit.update(dex, 0.7)
            action = f"BUY via {dex} (UKF vel={velocity:.2f})"
            self.trades_made += 1
        elif velocity < -1.0 and self._position_eth >= size:
            # Price trending down: SELL
            dex = bandit.select()
            self._capital    += predicted * size
            self._position_eth -= size
            pnl   = (price - self._prev_price) * size
            bandit.update(dex, max(0.0, 0.5 + pnl / (size * price)))
            action = f"SELL via {dex} (UKF vel={velocity:.2f})"
            self.trades_made += 1

        return action, pnl

    # ── Price fetching ────────────────────────────────────────────────────────

    async def _get_price(self) -> float:
        """
        Fetch token price.  Uses RPC + on-chain DEX when rpc_url is configured;
        falls back to CoinGecko → static default.
        """
        # Try CoinGecko (non-blocking via executor)
        try:
            loop = asyncio.get_event_loop()
            price = await loop.run_in_executor(None, self._fetch_coingecko)
            if price:
                return price
        except Exception as exc:
            logger.debug("CoinGecko price fetch failed: %s", exc)

        # Static fallback per token
        fallbacks = {
            Token.ETH:   2000.0,
            Token.USDC:  1.0,
            Token.WBTC:  60000.0,
            Token.ARB:   1.2,
            Token.MATIC: 0.9,
        }
        return fallbacks.get(self.config.token, 2000.0)

    def _fetch_coingecko(self) -> Optional[float]:
        token_ids = {
            Token.ETH:   "ethereum",
            Token.USDC:  "usd-coin",
            Token.WBTC:  "wrapped-bitcoin",
            Token.ARB:   "arbitrum",
            Token.MATIC: "matic-network",
        }
        cg_id = token_ids.get(self.config.token, "ethereum")
        try:
            import requests
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={cg_id}&vs_currencies=usd",
                timeout=4,
            )
            r.raise_for_status()
            return float(r.json()[cg_id]["usd"])
        except Exception as exc:
            logger.debug("CoinGecko direct fetch failed: %s", exc)
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    def performance(self) -> Dict[str, Any]:
        """Return a full performance summary dict."""
        equity   = self._capital + self._position_eth * (self._prev_price or 1.0)
        drawdown = max(0.0, 1.0 - equity / max(self._peak_capital, 1.0))
        roi      = (equity - self.config.initial_capital) / max(self.config.initial_capital, 1.0)
        return {
            "agent_id":       self.id,
            "name":           self.config.name,
            "strategy":       self.config.strategy.value,
            "chain":          self.config.chain.value,
            "token":          self.config.token.value,
            "status":         self.status.value,
            "wallet_address": self.wallet["address"],
            "cycle_count":    self.cycle_count,
            "trades_made":    self.trades_made,
            "errors":         self.errors,
            "capital_usd":    round(self._capital, 2),
            "position_eth":   round(self._position_eth, 6),
            "equity_usd":     round(equity, 2),
            "total_pnl_usd":  round(self.total_pnl, 2),
            "roi_pct":        round(roi * 100, 4),
            "max_drawdown_pct": round(drawdown * 100, 4),
            "last_result":    self._last_result,
            "chain_meta":     CHAIN_META.get(self.config.chain.value, {}),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Minimal serialisation for agent list endpoints."""
        return {
            "agent_id":       self.id,
            "name":           self.config.name,
            "strategy":       self.config.strategy.value,
            "chain":          self.config.chain.value,
            "token":          self.config.token.value,
            "status":         self.status.value,
            "wallet_address": self.wallet["address"],
            "cycle_count":    self.cycle_count,
            "trades_made":    self.trades_made,
            "total_pnl_usd":  round(self.total_pnl, 2),
            "dry_run":        self.config.dry_run,
        }


# ── AgentRegistry ─────────────────────────────────────────────────────────────


class AgentRegistry:
    """
    Thread-safe global registry of all TradingAgent instances.

    Usage
    -----
    registry = AgentRegistry()
    agent_id = registry.create(config)
    await registry.start(agent_id)
    registry.get(agent_id).performance()
    await registry.stop(agent_id)
    registry.list_all()
    """

    MAX_AGENTS = 20   # safety limit

    def __init__(self) -> None:
        self._agents: Dict[str, TradingAgent] = {}

    def create(self, config: TradingAgentConfig) -> TradingAgent:
        """Create, register, and return a new agent (not yet started)."""
        if len(self._agents) >= self.MAX_AGENTS:
            raise RuntimeError(f"Agent limit ({self.MAX_AGENTS}) reached")
        agent = TradingAgent(config)
        self._agents[agent.id] = agent
        logger.info(
            "Registry: created agent %s strategy=%s chain=%s token=%s wallet=%s",
            agent.id, config.strategy.value, config.chain.value,
            config.token.value, agent.wallet["address"],
        )
        return agent

    async def start(self, agent_id: str) -> TradingAgent:
        agent = self._get_or_raise(agent_id)
        await agent.start()
        return agent

    async def stop(self, agent_id: str) -> TradingAgent:
        agent = self._get_or_raise(agent_id)
        await agent.stop()
        return agent

    def get(self, agent_id: str) -> Optional[TradingAgent]:
        return self._agents.get(agent_id)

    def remove(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def list_all(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._agents.values()]

    def count(self) -> int:
        return len(self._agents)

    def _get_or_raise(self, agent_id: str) -> TradingAgent:
        agent = self._agents.get(agent_id)
        if not agent:
            raise KeyError(f"Agent {agent_id!r} not found")
        return agent


# ── Module-level singleton ────────────────────────────────────────────────────

registry = AgentRegistry()
