"""
engine/dex/liquidity_monitor.py — DEX liquidity price monitor.

Updated to use the shared PriceCache so it never duplicates the CoinGecko
fetch that market_data.py already performs in the same cycle.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from engine.price_cache import price_cache


class LiquidityMonitor:
    """
    Monitors DEX liquidity price for ETH/USD.

    Falls back to CoinGecko (shared with MarketData via PriceCache).
    In Phase 2, this will query the Rust dex-oracle sidecar first,
    which returns live Uniswap V3 + SushiSwap prices with sub-100 ms
    latency.
    """

    COINGECKO_URL = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=ethereum&vs_currencies=usd"
    )

    def __init__(self):
        self._dex_oracle_url: Optional[str] = "http://localhost:9001"

    # ── synchronous ───────────────────────────────────────────────────────────

    def get_price(self) -> Optional[float]:
        """
        Return the current ETH/USD price.

        Priority:
          1. Rust dex-oracle sidecar (Phase 2 — sub-100 ms, on-chain)
          2. Shared PriceCache (CoinGecko, 5 s TTL)
          3. Hard fallback

        The PriceCache is shared with MarketData — if market.get_price()
        was called earlier this cycle the result is returned instantly.
        """
        # Phase 2: attempt dex-oracle first (fast path)
        oracle_price = self._query_dex_oracle()
        if oracle_price is not None:
            return oracle_price

        # Fallback: shared cache (avoids duplicate CoinGecko fetch)
        import requests
        def _fetch() -> float:
            r = requests.get(self.COINGECKO_URL, timeout=5)
            r.raise_for_status()
            return float(r.json()["ethereum"]["usd"])

        return price_cache.get(_fetch)

    def _query_dex_oracle(self) -> Optional[float]:
        """
        Query the Rust dex-oracle sidecar for a blended DEX price.
        Returns None if the sidecar is not running (graceful fallback).
        """
        try:
            import requests
            r = requests.get(
                f"{self._dex_oracle_url}/prices",
                timeout=0.5,    # very tight timeout — sidecar must be local
            )
            if r.status_code == 200:
                data = r.json()
                prices = [v for v in data.values() if isinstance(v, (int, float)) and v > 0]
                if prices:
                    return max(prices)   # use the best available price
        except Exception:
            pass   # sidecar not running — fall through to CoinGecko
        return None

    # ── async ─────────────────────────────────────────────────────────────────

    async def get_price_async(self) -> Optional[float]:
        """
        Async version for use inside the asyncio trading loop.
        Queries the dex-oracle first (non-blocking), then falls back to
        the shared cache.
        """
        # Try dex-oracle first via httpx
        try:
            import httpx
            async with httpx.AsyncClient(timeout=0.5) as client:
                r = await client.get(f"{self._dex_oracle_url}/prices")
                if r.status_code == 200:
                    data = r.json()
                    prices = [v for v in data.values() if isinstance(v, (int, float)) and v > 0]
                    if prices:
                        return max(prices)
        except Exception:
            pass

        # Fallback: run sync get_price in executor (uses shared cache)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_price)
