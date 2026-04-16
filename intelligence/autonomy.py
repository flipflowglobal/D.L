"""
AUREON Autonomous Agent Loop — with cross-language fault-tolerant supervisor.

Architecture:
  - Supervisor manages Rust sidecars (dex-oracle :9001, tx-engine :9002)
  - ResilientPriceEngine provides 4-layer price fallback:
      Rust → Python async RPC → CoinGecko → static fallback
  - A Rust crash never crashes Python; Python exceptions never stop Rust
  - Sidecar auto-restarts with exponential back-off (max 5 retries)

Performance stack:
  - uvloop (libuv) event loop
  - Cython .so hot-path modules (portfolio, risk_manager, mean_reversion)
  - tokio::join! 4 concurrent RPC calls inside Rust dex-oracle
  - Shared PriceCache singleton — 1 CoinGecko call per TTL window
  - asyncio.gather() for concurrent Python fallback and memory writes

Start via POST /aureon/start?agent_id=AUREON
Stop  via POST /aureon/stop
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from intelligence.memory import memory
from engine.price_cache  import price_cache

load_dotenv()

logger = logging.getLogger("aureon.agent")

CYCLE_INTERVAL = int(os.getenv("SCAN_INTERVAL",   "30"))
TRADE_SIZE_ETH = float(os.getenv("TRADE_SIZE_ETH", "0.05"))
MIN_PROFIT_USD = float(os.getenv("MIN_PROFIT_USD", "2.0"))


class AgentLoop:
    """
    Autonomous trading loop that runs as a background asyncio Task.

    Fully async — never blocks the FastAPI event loop.
    One price fetch per cycle (shared via PriceCache with trade.py).
    Concurrent DEX queries via scan_async().
    """

    def __init__(self):
        self.running     = False
        self.cycle_count = 0
        self._supervisor  = None
        self._price_engine = None

    # ── engine init ───────────────────────────────────────────────────────────

    def _build_engine(self) -> dict:
        """Build engine modules (called once; synchronous import is OK at startup)."""
        from engine.market_data                  import MarketData
        from engine.portfolio                    import Portfolio
        from engine.risk_manager                 import RiskManager
        from engine.strategies.mean_reversion    import MeanReversionStrategy
        from engine.arbitrage.arbitrage_scanner  import ArbitrageScanner
        from engine.dex.liquidity_monitor        import LiquidityMonitor
        from engine.execution.executor           import Executor

        rpc = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
        return {
            "market":    MarketData(),
            "portfolio": Portfolio(initial_usd=float(os.getenv("INITIAL_USD", "10000"))),
            "risk":      RiskManager(),
            "strategy":  MeanReversionStrategy(window=12, threshold=0.015),
            "arb":       ArbitrageScanner(rpc_url=rpc),
            "liquidity": LiquidityMonitor(),
            "executor":  Executor(),
        }

    # ── async cycle ───────────────────────────────────────────────────────────

    async def _run_cycle_async(self, eng: dict, agent_id: str) -> dict:
        """
        Execute one trading cycle — fully async.

        Step 1: Fetch ETH price once (async, shared cache).
                All subsequent price lookups in this cycle hit the cache.
        Step 2: Run DEX arbitrage scan concurrently (Uniswap V3 × 3 fee
                tiers + SushiSwap — all 4 RPC calls in parallel).
        Step 3: Apply arbitrage or mean-reversion signal.
        """
        market    = eng["market"]
        portfolio = eng["portfolio"]
        risk      = eng["risk"]
        strategy  = eng["strategy"]
        arb       = eng["arb"]
        executor  = eng["executor"]

        # ── Step 1: price via resilient engine (Rust → Python → CoinGecko → fallback)
        price_cache.invalidate()
        if self._price_engine:
            prices    = await self._price_engine.get_prices()
            eth_price = prices.get("uniswap_v3") or prices.get("sushiswap") or \
                        next(iter(prices.values()), None)
        else:
            eth_price = await market.get_price_async()

        if not eth_price:
            return {"status": "no_price"}

        # ── Step 2: concurrent DEX scan (async gather inside scan_async) ──────
        opps = await arb.scan_async(eth_price)
        action = "HOLD"

        # ── Step 3: trade decision ────────────────────────────────────────────
        if opps and risk.can_trade():
            opp = opps[0]
            est = opp["est_profit_pct"] / 100.0 * TRADE_SIZE_ETH * eth_price
            if est >= MIN_PROFIT_USD:
                executor.execute_buy(portfolio, eth_price, TRADE_SIZE_ETH)
                risk.record_trade()
                action = f"ARB_BUY est_profit=${est:.2f}"
        else:
            sig = strategy.signal(eth_price)
            if sig == "BUY" and risk.can_trade():
                executor.execute_buy(portfolio, eth_price, TRADE_SIZE_ETH)
                risk.record_trade()
                action = "BUY"
            elif sig == "SELL" and risk.can_trade():
                executor.execute_sell(portfolio, eth_price, TRADE_SIZE_ETH)
                risk.record_trade()
                action = "SELL"

        pe_stats   = self._price_engine.stats() if self._price_engine else {}

        return {
            "status":      "ok",
            "eth_price":   eth_price,
            "action":      action,
            "portfolio":   portfolio.summary(),
            "cache_stats": price_cache.stats(),
            "price_engine": pe_stats,
            "sidecars": {
                "dex_ok": self._supervisor.dex_ok() if self._supervisor else False,
                "tx_ok":  self._supervisor.tx_ok()  if self._supervisor else False,
            },
        }

    # ── async run loop ────────────────────────────────────────────────────────

    async def run(self, agent_id: str) -> None:
        logger.info("Agent %s starting …", agent_id)
        await memory.init_db()

        # ── Start cross-language supervisor ────────────────────────────────────
        try:
            from supervisor import Supervisor
            from engine.resilient_price_engine import ResilientPriceEngine
            self._supervisor   = Supervisor()
            await self._supervisor.start()
            self._price_engine = ResilientPriceEngine(supervisor=self._supervisor)
            logger.info("Supervisor started — Rust sidecars managed")
        except Exception as exc:
            logger.warning("Supervisor unavailable (%s) — Python-only mode", exc)
            try:
                from engine.resilient_price_engine import ResilientPriceEngine
                self._price_engine = ResilientPriceEngine(supervisor=None)
            except Exception as exc:
                logger.debug("Fallback ResilientPriceEngine init failed: %s", exc)
                self._price_engine = None

        # Build engine in executor (one-time synchronous import + init)
        loop = asyncio.get_running_loop()
        eng  = await loop.run_in_executor(None, self._build_engine)

        self.cycle_count = 0
        await memory.store(agent_id, "status",     "running")
        await memory.store(agent_id, "started_at", datetime.now(timezone.utc).isoformat())

        while self.running:
            self.cycle_count += 1
            ts = datetime.now(timezone.utc).isoformat()

            try:
                result = await self._run_cycle_async(eng, agent_id)
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
                print(f"[AUREON] Cycle error: {exc}")

            # Persist state (3 concurrent SQLite writes)
            await asyncio.gather(
                memory.store(agent_id, "last_cycle",  str(self.cycle_count)),
                memory.store(agent_id, "last_run",    ts),
                memory.store(agent_id, "last_result", str(result)),
            )

            print(
                f"[AUREON] {agent_id}"
                f"  cycle={self.cycle_count}"
                f"  {result.get('action', '?')}"
                f"  eth=${result.get('eth_price', 0):,.0f}"
                f"  cache={result.get('cache_stats', {}).get('hit_ratio', 0):.0%}"
            )

            await asyncio.sleep(CYCLE_INTERVAL)

        await memory.store(agent_id, "status", "stopped")
        logger.info("Agent %s stopped after %d cycles", agent_id, self.cycle_count)

        # ── Graceful shutdown of sidecars ──────────────────────────────────────
        if self._supervisor:
            await self._supervisor.stop()
        if self._price_engine:
            await self._price_engine.close()


loop = AgentLoop()
