"""
AUREON Autonomous Agent Loop.

Integrates the trading engine into the FastAPI lifecycle.
Start via POST /aureon/start?agent_id=AUREON
Stop  via POST /aureon/stop
"""

import asyncio
import os
import time
from datetime import datetime

from dotenv import load_dotenv

from intelligence.memory import memory

load_dotenv()

CYCLE_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))   # seconds
TRADE_SIZE_ETH = float(os.getenv("TRADE_SIZE_ETH", "0.05"))
MIN_PROFIT_USD = float(os.getenv("MIN_PROFIT_USD", "2.0"))


class AgentLoop:
    """
    Autonomous trading loop that runs as a background asyncio Task.
    Uses run_in_executor so the blocking trading engine doesn't
    stall the FastAPI event loop.
    """

    def __init__(self):
        self.running     = False
        self.cycle_count = 0

    # ── engine init (lazy — happens inside the async task) ────────────────────

    def _build_engine(self):
        from engine.market_data                     import MarketData
        from engine.portfolio                       import Portfolio
        from engine.risk_manager                    import RiskManager
        from engine.strategies.mean_reversion       import MeanReversionStrategy
        from engine.arbitrage.arbitrage_scanner     import ArbitrageScanner
        from engine.dex.liquidity_monitor           import LiquidityMonitor
        from engine.execution.executor              import Executor

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

    # ── single synchronous cycle (run in thread pool) ─────────────────────────

    def _run_cycle(self, eng: dict, agent_id: str) -> dict:
        market    = eng["market"]
        portfolio = eng["portfolio"]
        risk      = eng["risk"]
        strategy  = eng["strategy"]
        arb       = eng["arb"]
        liquidity = eng["liquidity"]
        executor  = eng["executor"]

        eth_price = market.get_price()
        if eth_price is None:
            return {"status": "no_price"}

        dex_price = liquidity.get_price()
        if dex_price is None:
            return {"status": "no_dex_price"}

        opps = arb.scan(eth_price)
        action = "HOLD"

        if opps and risk.can_trade():
            opp = opps[0]
            est = opp["est_profit_pct"] / 100 * TRADE_SIZE_ETH * eth_price
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

        return {
            "status":    "ok",
            "eth_price": eth_price,
            "action":    action,
            "portfolio": portfolio.summary(),
        }

    # ── async run loop ────────────────────────────────────────────────────────

    async def run(self, agent_id: str):
        print(f"[AUREON] Agent {agent_id} starting …")
        await memory.init_db()

        loop = asyncio.get_event_loop()
        eng  = await loop.run_in_executor(None, self._build_engine)

        self.cycle_count = 0
        await memory.store(agent_id, "status", "running")
        await memory.store(agent_id, "started_at", datetime.utcnow().isoformat())

        while self.running:
            self.cycle_count += 1
            ts = datetime.utcnow().isoformat()

            try:
                result = await loop.run_in_executor(
                    None, self._run_cycle, eng, agent_id
                )
            except Exception as e:
                result = {"status": "error", "error": str(e)}
                print(f"[AUREON] Cycle error: {e}")

            await memory.store(agent_id, "last_cycle",  str(self.cycle_count))
            await memory.store(agent_id, "last_run",    ts)
            await memory.store(agent_id, "last_result", str(result))

            print(
                f"[AUREON] {agent_id}  cycle={self.cycle_count}"
                f"  {result.get('action','?')}"
                f"  eth=${result.get('eth_price', 0):,.0f}"
            )

            await asyncio.sleep(CYCLE_INTERVAL)

        await memory.store(agent_id, "status", "stopped")
        print(f"[AUREON] Agent {agent_id} stopped after {self.cycle_count} cycles")


loop = AgentLoop()
