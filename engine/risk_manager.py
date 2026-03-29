class RiskManager:
    """
    Simple guard against over-trading.
    can_trade() returns False once max_daily_trades is reached.
    Call record_trade() after every executed trade.
    """

    def __init__(self, max_daily_trades: int = 50, max_position_usd: float = 5_000.0):
        self.max_daily_trades = max_daily_trades
        self.max_position_usd = max_position_usd
        self.trade_count = 0

    def can_trade(self) -> bool:
        return self.trade_count < self.max_daily_trades

    def record_trade(self):
        self.trade_count += 1

    def reset(self):
        self.trade_count = 0
