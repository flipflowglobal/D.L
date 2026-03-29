import random
from typing import List, Optional


class ArbitrageScanner:
    """
    Simulates a cross-DEX arbitrage scan.
    In production, replace the random price simulation with real DEX API calls.
    """

    DEX_NAMES = ["uniswap_v3", "sushiswap", "curve"]

    def __init__(self, spread_threshold: float = 0.005, noise: float = 0.01):
        self.spread_threshold = spread_threshold
        self.noise = noise

    def scan(self, price: float) -> Optional[List[dict]]:
        dex_prices = {
            dex: price * (1 + random.uniform(-self.noise, self.noise))
            for dex in self.DEX_NAMES
        }

        min_dex = min(dex_prices, key=dex_prices.get)
        max_dex = max(dex_prices, key=dex_prices.get)
        spread = (dex_prices[max_dex] - dex_prices[min_dex]) / dex_prices[min_dex]

        if spread >= self.spread_threshold:
            return [{
                "buy_on": min_dex,
                "buy_price": round(dex_prices[min_dex], 4),
                "sell_on": max_dex,
                "sell_price": round(dex_prices[max_dex], 4),
                "spread_pct": round(spread * 100, 4),
            }]

        return None
