import requests
from typing import Optional


class LiquidityMonitor:
    """
    Monitors DEX liquidity price for ETH/USD.
    In production, point this at a Uniswap subgraph or on-chain oracle.
    Falls back to CoinGecko.
    """

    COINGECKO_URL = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=ethereum&vs_currencies=usd"
    )

    def __init__(self):
        pass

    def get_price(self) -> Optional[float]:
        try:
            r = requests.get(self.COINGECKO_URL, timeout=5)
            r.raise_for_status()
            return float(r.json()["ethereum"]["usd"])
        except Exception as e:
            print(f"[LiquidityMonitor] Price fetch failed: {e}")
            return None
