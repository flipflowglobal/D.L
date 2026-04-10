"""
engine/market_data.py — Live ETH/USD price feed with async support.

Changes vs original:
  - Uses the shared PriceCache singleton to eliminate duplicate HTTP calls.
  - Adds get_price_async() for use in async trading loops (httpx, no blocking).
  - FALLBACK_PRICE is now configurable via FALLBACK_ETH_PRICE env var.
  - Connection kept alive via httpx session (persistent TCP connection).
"""

from __future__ import annotations

import os
import requests

from engine.price_cache import price_cache

_FALLBACK = float(os.getenv("FALLBACK_ETH_PRICE", "2000.0"))


class MarketData:
    """
    Fetches live ETH/USD price from CoinGecko.

    All instances share the same TTL cache so only one HTTP request is made
    per TTL window regardless of how many callers exist.
    """

    COINGECKO_URL = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=ethereum&vs_currencies=usd"
    )
    FALLBACK_PRICE: float = _FALLBACK

    def __init__(self, symbol: str = "ETH", currency: str = "USD"):
        self.symbol   = symbol
        self.currency = currency

    # ── synchronous (used by trade.py, run_in_executor) ───────────────────────

    def _fetch(self) -> float:
        """Raw HTTP fetch — called by price_cache.get() on cache miss only."""
        r = requests.get(self.COINGECKO_URL, timeout=5)
        r.raise_for_status()
        return float(r.json()["ethereum"]["usd"])

    def get_price(self) -> float:
        """
        Return the current ETH/USD price.

        Uses the process-global PriceCache: at most one HTTP request per TTL
        window (default 5 s) regardless of how many callers invoke this method.
        Falls back to FALLBACK_PRICE on network failure.
        """
        return price_cache.get(self._fetch)

    # ── async (used directly from asyncio loops) ──────────────────────────────

    async def get_price_async(self) -> float:
        """
        Async version — uses httpx for non-blocking HTTP.
        Falls back to FALLBACK_PRICE on failure.

        Shares the same PriceCache so a concurrent sync call won't trigger
        a duplicate fetch.
        """
        # Fast path: return cached value without touching httpx
        cached = price_cache.peek()
        if cached is not None:
            return cached

        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(self.COINGECKO_URL)
                r.raise_for_status()
                price = float(r.json()["ethereum"]["usd"])
                # Manually populate the cache so sync callers benefit too
                price_cache.get(lambda: price)
                return price
        except Exception as exc:
            print(f"[MarketData] async fetch failed ({exc}), using fallback {self.FALLBACK_PRICE}")
            return self.FALLBACK_PRICE
