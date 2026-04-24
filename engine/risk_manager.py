"""
engine/risk_manager.py — Trading guardrails.

Enforces:
  * max_daily_trades  — hard cap on executions per calendar day (UTC).
  * max_position_usd  — maximum value of a single open position.

The daily trade counter auto-resets at UTC midnight.  Call record_trade()
after every executed trade and pass the position value so the position
limit can also be enforced.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("aureon.risk_manager")


class RiskManager:
    """
    Simple guard against over-trading and over-sizing positions.

    Attributes:
        max_daily_trades: maximum executed trades per UTC calendar day.
        max_position_usd: maximum USD value allowed for a single position.
    """

    def __init__(
        self,
        max_daily_trades: int = 20,
        max_position_usd: float = 2_000.0,
    ) -> None:
        self.max_daily_trades = max_daily_trades
        self.max_position_usd = max_position_usd

        self._trade_count: int = 0
        self._reset_day: int   = self._today()

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _today() -> int:
        """Return today's UTC date as an integer YYYYMMDD."""
        now = datetime.now(timezone.utc)
        return now.year * 10000 + now.month * 100 + now.day

    def _maybe_reset(self) -> None:
        """Auto-reset counter when the UTC calendar day rolls over."""
        today = self._today()
        if today != self._reset_day:
            logger.info(
                "Daily trade counter reset (was %d, day %d → %d)",
                self._trade_count, self._reset_day, today,
            )
            self._trade_count = 0
            self._reset_day   = today

    # ── Public API ────────────────────────────────────────────────────────────

    def can_trade(self, position_value_usd: float = 0.0) -> bool:
        """
        Return True if trading is permitted under current risk limits.

        Args:
            position_value_usd: estimated USD value of the prospective trade.
                                Pass 0.0 to skip the position-size check.

        Returns:
            True if both the daily trade count and position size limits allow
            a new trade to be executed.
        """
        self._maybe_reset()

        if self._trade_count >= self.max_daily_trades:
            logger.info(
                "Daily trade limit reached (%d/%d)", self._trade_count, self.max_daily_trades
            )
            return False

        if position_value_usd > self.max_position_usd:
            logger.info(
                "Position size $%.2f exceeds limit $%.2f",
                position_value_usd, self.max_position_usd,
            )
            return False

        return True

    def record_trade(self) -> None:
        """Increment the daily trade counter (call after every executed trade)."""
        self._maybe_reset()
        self._trade_count += 1
        logger.debug(
            "Trade recorded (%d/%d today)", self._trade_count, self.max_daily_trades
        )

    def reset(self) -> None:
        """Manually reset the daily trade counter (e.g. for testing)."""
        self._trade_count = 0
        self._reset_day   = self._today()
        logger.info("Trade counter manually reset")

    @property
    def trade_count(self) -> int:
        """Current daily trade count (auto-resets at UTC midnight)."""
        self._maybe_reset()
        return self._trade_count

    @trade_count.setter
    def trade_count(self, value: int) -> None:
        """Allow direct assignment for testing and external integrations."""
        self._trade_count = value

    def status(self) -> dict:
        """Return a snapshot of current risk state."""
        self._maybe_reset()
        return {
            "trade_count":     self._trade_count,
            "max_daily_trades": self.max_daily_trades,
            "max_position_usd": self.max_position_usd,
            "reset_day":        self._reset_day,
        }

    def kelly_position_size(
        self,
        kelly_fraction: float,
        capital_usd: float,
        max_fraction: float = 0.25,
    ) -> float:
        """
        Compute Kelly-sized position from a pre-computed Kelly fraction.

        kelly_fraction: f* from BellmanFord MC (0–1), already quarter-Kelly
        capital_usd:    available capital in USD
        max_fraction:   hard cap as fraction of capital (default 25 %)
        Returns:        position size in USD, capped at max_position_usd
        """
        f   = float(max(min(kelly_fraction, max_fraction), 0.0))
        pos = f * float(capital_usd)
        capped = min(pos, float(self.max_position_usd))
        logger.debug(
            "Kelly position: f=%.4f raw=%.2f capped=%.2f", f, pos, capped
        )
        return capped
