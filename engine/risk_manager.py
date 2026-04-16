from datetime import date


class RiskManager:
    """
    Simple guard against over-trading.
    can_trade() returns False once max_daily_trades is reached.
    Call record_trade() after every executed trade.

    The trade counter resets automatically at the start of each new calendar
    day (UTC) so the guard never blocks indefinitely.
    """

    def __init__(self, max_daily_trades: int = 50, max_position_usd: float = 5_000.0):
        self.max_daily_trades = max_daily_trades
        self.max_position_usd = max_position_usd
        self.trade_count = 0
        self._last_reset_date: date = date.today()

    def can_trade(self) -> bool:
        self._maybe_reset()
        return self.trade_count < self.max_daily_trades

    def record_trade(self):
        self._maybe_reset()
        self.trade_count += 1

    def reset(self):
        self.trade_count = 0
        self._last_reset_date = date.today()

    def _maybe_reset(self):
        """Reset the trade counter if we've crossed into a new calendar day."""
        today = date.today()
        if today != self._last_reset_date:
            self.trade_count = 0
            self._last_reset_date = today
