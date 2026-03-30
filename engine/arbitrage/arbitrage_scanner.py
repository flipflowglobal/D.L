"""
Cross-DEX Arbitrage Scanner.

Queries Uniswap V3 and SushiSwap V2 simultaneously for the ETH/USDC price.
If the spread between them exceeds `spread_threshold`, an opportunity dict
is returned.  Falls back to CoinGecko simulation when RPC is unavailable.
"""

import os
import random
import requests
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class ArbitrageScanner:
    """
    Live cross-DEX arbitrage scanner.

    Pass rpc_url to enable on-chain pricing via Uniswap V3 + SushiSwap.
    If rpc_url is None or connections fail, falls back to simulated prices.
    """

    COINGECKO_URL = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=ethereum&vs_currencies=usd"
    )

    # sentinel so we can tell "caller didn't pass rpc_url" from "caller passed None"
    _UNSET = object()

    def __init__(
        self,
        rpc_url=_UNSET,
        spread_threshold: float = 0.003,   # 0.3 % minimum to flag an opportunity
    ):
        self.spread_threshold = spread_threshold
        self._uni = None
        self._sushi = None

        # Only fall back to env-var RPC when the caller didn't supply rpc_url at all.
        # Passing rpc_url=None explicitly means "simulation mode — no RPC".
        if rpc_url is ArbitrageScanner._UNSET:
            rpc = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
        else:
            rpc = rpc_url
        if rpc:
            try:
                from engine.dex.uniswap_v3 import UniswapV3
                from engine.dex.sushiswap import SushiSwap
                self._uni = UniswapV3(rpc)
                self._sushi = SushiSwap(rpc)
                if not self._uni.is_connected():
                    raise ConnectionError("Uniswap RPC not reachable")
                print("[ArbitrageScanner] On-chain mode (Uniswap V3 + SushiSwap)")
            except Exception as e:
                print(f"[ArbitrageScanner] On-chain init failed ({e}), using simulation")
                self._uni = None
                self._sushi = None
        else:
            print("[ArbitrageScanner] No RPC_URL — using simulation mode")

    # ── price helpers ─────────────────────────────────────────────────────────

    def _coingecko_price(self) -> Optional[float]:
        try:
            r = requests.get(self.COINGECKO_URL, timeout=5)
            r.raise_for_status()
            return float(r.json()["ethereum"]["usd"])
        except Exception:
            return None

    def _live_prices(self) -> dict:
        """Return {dex_name: price} from on-chain sources."""
        prices = {}
        if self._uni:
            p = self._uni.get_best_eth_price()
            if p:
                prices["uniswap_v3"] = p
        if self._sushi:
            p = self._sushi.get_eth_price_usdc()
            if p:
                prices["sushiswap"] = p
        return prices

    def _simulated_prices(self, base_price: float) -> dict:
        """Return synthetic prices with random ±1 % noise per DEX."""
        return {
            "uniswap_v3": base_price * (1 + random.uniform(-0.01, 0.01)),
            "sushiswap":  base_price * (1 + random.uniform(-0.01, 0.01)),
            "curve":      base_price * (1 + random.uniform(-0.005, 0.005)),
        }

    # ── public interface ──────────────────────────────────────────────────────

    def get_prices(self) -> dict:
        """Return current ETH/USD prices from all available sources."""
        if self._uni or self._sushi:
            prices = self._live_prices()
            if prices:
                return prices
        # Fallback
        base = self._coingecko_price() or 2000.0
        return self._simulated_prices(base)

    def scan(self, price: Optional[float] = None) -> Optional[list]:
        """
        Scan for arbitrage opportunities.

        Args:
            price: Optional override base price (used in simulation fallback).

        Returns:
            List of opportunity dicts if spread >= threshold, else None.
        """
        prices = self.get_prices()
        if not prices:
            return None

        min_dex = min(prices, key=prices.get)
        max_dex = max(prices, key=prices.get)
        low  = prices[min_dex]
        high = prices[max_dex]
        spread = (high - low) / low

        if spread >= self.spread_threshold:
            # Rough profit estimate: spread minus 0.3 % Uniswap fee on each leg
            gross_profit_pct = spread - 0.006  # 2 × 0.3 %
            return [{
                "buy_on":         min_dex,
                "buy_price":      round(low, 4),
                "sell_on":        max_dex,
                "sell_price":     round(high, 4),
                "spread_pct":     round(spread * 100, 4),
                "est_profit_pct": round(gross_profit_pct * 100, 4),
                "all_prices":     {k: round(v, 4) for k, v in prices.items()},
            }]

        return None
