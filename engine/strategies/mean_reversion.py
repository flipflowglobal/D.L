from collections import deque


class MeanReversionStrategy:
    """
    Generates BUY / SELL / HOLD signals based on price deviation from
    a rolling mean.  Signals are produced only once the window is full.
    """

    def __init__(self, window: int = 10, threshold: float = 0.02):
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        if threshold <= 0:
            raise ValueError(f"threshold must be > 0, got {threshold}")
        self.prices: deque = deque(maxlen=window)
        self.threshold = threshold

    def signal(self, price: float) -> str:
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
