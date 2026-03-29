import requests


class MarketData:
    """
    Fetches live ETH/USD price from CoinGecko.
    Falls back to a safe default if the request fails.
    """

    COINGECKO_URL = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=ethereum&vs_currencies=usd"
    )
    FALLBACK_PRICE = 2000.0

    def __init__(self, symbol: str = "ETH", currency: str = "USD"):
        self.symbol = symbol
        self.currency = currency

    def get_price(self) -> float:
        try:
            r = requests.get(self.COINGECKO_URL, timeout=5)
            r.raise_for_status()
            return float(r.json()["ethereum"]["usd"])
        except Exception as e:
            print(f"[MarketData] Price fetch failed ({e}), using fallback {self.FALLBACK_PRICE}")
            return self.FALLBACK_PRICE
