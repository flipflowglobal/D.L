"""
engine/strategies/mean_reversion.py — Rolling mean-reversion signal generator.

Generates BUY / SELL / HOLD signals based on deviation of the latest price
from a rolling window mean.  Signals are withheld until the window is full
to avoid acting on insufficient data.
"""

from __future__ import annotations

from collections import deque


class MeanReversionStrategy:
    """
    Generates BUY / SELL / HOLD signals based on price deviation from
    a rolling mean.  Signals are produced only once the window is full.

    Args:
        window:    number of price observations to track (rolling window).
        threshold: fractional deviation required to trigger a signal.
                   Default 0.02 = 2 % from the mean.
    """

    def __init__(self, window: int = 10, threshold: float = 0.02) -> None:
        self.prices: deque[float] = deque(maxlen=window)
        self.threshold = threshold

    def signal(self, price: float) -> str:
        """
        Consume *price* and return a trading signal.

        Returns:
            "BUY"  — price is below the rolling mean by more than *threshold*.
            "SELL" — price is above the rolling mean by more than *threshold*.
            "HOLD" — window not yet full, or deviation within *threshold*.
        """
        self.prices.append(price)

        if len(self.prices) < self.prices.maxlen:
            return "HOLD"

        mean = sum(self.prices) / len(self.prices)
        if mean <= 0:
            return "HOLD"

        deviation = (price - mean) / mean

        if deviation < -self.threshold:
            return "BUY"
        if deviation > self.threshold:
            return "SELL"
        return "HOLD"
