import json
import os
from datetime import datetime
from typing import Optional

TRADE_LOG_FILE = os.path.join(
    os.path.dirname(__file__), "..", "vault", "trade_log.json"
)


class Portfolio:
    """
    Tracks USD and ETH balances, records every trade, and computes P&L.
    Works in both paper and live modes — swap_executor fills in tx_hash for live.
    """

    def __init__(self, initial_usd: float = 10_000.0):
        self.initial_usd = initial_usd
        self.balance_usd = initial_usd
        self.balance_eth = 0.0
        self.trades: list = []

    # ── internal balance methods ──────────────────────────────────────────────

    def buy(self, price: float, amount: float) -> bool:
        cost = price * amount
        if self.balance_usd < cost:
            return False
        self.balance_usd -= cost
        self.balance_eth += amount
        return True

    def sell(self, price: float, amount: float) -> bool:
        if self.balance_eth < amount:
            return False
        self.balance_eth -= amount
        self.balance_usd += price * amount
        return True

    # ── trade logging ─────────────────────────────────────────────────────────

    def log_trade(
        self,
        side: str,
        price: float,
        amount: float,
        tx_hash: Optional[str] = None,
    ) -> None:
        """Record a trade entry (called by both paper and live executors)."""
        # Apply balance change for paper trades (live trades are settled on-chain)
        if tx_hash is None:
            if side in ("BUY", "ARB_BUY"):
                self.buy(price, amount)
            elif side in ("SELL", "ARB_SELL"):
                self.sell(price, amount)

        self.trades.append({
            "timestamp": datetime.utcnow().isoformat(),
            "side":      side,
            "price_usd": round(price, 4),
            "amount_eth": round(amount, 6),
            "value_usd":  round(price * amount, 4),
            "tx_hash":    tx_hash,
        })

    # ── summary & persistence ─────────────────────────────────────────────────

    def summary(self) -> dict:
        total_value = self.balance_usd + self.balance_eth * self._last_price()
        pnl = total_value - self.initial_usd
        return {
            "balance_usd":   round(self.balance_usd, 2),
            "balance_eth":   round(self.balance_eth, 6),
            "total_value":   round(total_value, 2),
            "pnl_usd":       round(pnl, 2),
            "pnl_pct":       round(pnl / self.initial_usd * 100, 3),
            "trade_count":   len(self.trades),
        }

    def _last_price(self) -> float:
        """Best-effort last known ETH price from trade history."""
        for t in reversed(self.trades):
            if t.get("price_usd") and t["price_usd"] > 0:
                return t["price_usd"]
        return 1.0  # non-zero: avoids wiping ETH value before first trade is recorded

    def save_trade_log(self, path: Optional[str] = None) -> None:
        target = path or TRADE_LOG_FILE
        os.makedirs(os.path.dirname(target), exist_ok=True)
        payload = {
            "saved_at":    datetime.utcnow().isoformat(),
            "initial_usd": self.initial_usd,
            "summary":     self.summary(),
            "trades":      self.trades,
        }
        with open(target, "w") as f:
            json.dump(payload, f, indent=2)
