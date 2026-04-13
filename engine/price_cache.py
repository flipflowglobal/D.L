"""
engine/price_cache.py — Thread-safe, async-aware TTL price cache.

Problem it solves:
    The trading loop was fetching the ETH/USD price 3–4 times per cycle
    from the same CoinGecko endpoint (market_data, liquidity_monitor, and
    the arb scanner fallback all call it independently).  Each call is a
    300–500 ms HTTP round-trip.

Solution:
    A process-global singleton that caches the last fetched price for
    TTL seconds.  Any subsequent call within the TTL window returns the
    cached value instantly (0 µs HTTP overhead).

    Thread-safe: uses threading.RLock (safe for both sync trade.py and
    async autonomy.py via run_in_executor).

Usage:
    from engine.price_cache import price_cache

    # In any price-fetching method:
    def get_price(self) -> float:
        return price_cache.get(self._fetch_from_api)

    # Force a fresh fetch:
    price_cache.invalidate()
"""

from __future__ import annotations

import threading
import time
import os
from typing import Callable, Optional


class _PriceCache:
    """
    Singleton TTL cache for ETH/USD spot price.

    - TTL defaults to 5 seconds (configurable via PRICE_CACHE_TTL env var).
    - Re-entrant lock (RLock) allows the same thread to call get() recursively.
    - Falls back to FALLBACK_ETH_PRICE if the first fetch raises.
    """

    _instance: Optional["_PriceCache"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "_PriceCache":
        with cls._init_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._price: Optional[float]  = None
                inst._timestamp: float         = 0.0
                inst._ttl: float               = float(os.getenv("PRICE_CACHE_TTL", "5.0"))
                inst._fallback: float          = float(os.getenv("FALLBACK_ETH_PRICE", "2000.0"))
                inst._lock = threading.RLock()
                inst._hits: int   = 0     # telemetry: cache hits
                inst._misses: int = 0     # telemetry: cache misses
                cls._instance = inst
        return cls._instance

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, fetch_fn: Callable[[], Optional[float]]) -> float:
        """
        Return the cached price if it is still fresh; otherwise call
        fetch_fn(), cache the result, and return it.

        Args:
            fetch_fn: Zero-argument callable that returns a float price or
                      None on failure.  Called at most once per TTL window.

        Returns:
            ETH/USD price as a float.  Never raises — falls back to
            FALLBACK_ETH_PRICE on consecutive failures.
        """
        with self._lock:
            now = time.monotonic()
            if self._price is not None and (now - self._timestamp) < self._ttl:
                self._hits += 1
                return self._price

            # Cache miss — fetch fresh price
            self._misses += 1
            try:
                result = fetch_fn()
                if result is not None and result > 0.0:
                    self._price     = float(result)
                    self._timestamp = now
                    return self._price
            except Exception as exc:
                print(f"[PriceCache] fetch_fn raised: {exc}")

            # Fetch failed — use stale value if available, else fallback
            if self._price is not None:
                print(f"[PriceCache] Using stale price ${self._price:,.2f} (fetch failed)")
                return self._price

            print(f"[PriceCache] No price available, using fallback ${self._fallback:,.2f}")
            return self._fallback

    def invalidate(self) -> None:
        """Force the next get() call to fetch a fresh price."""
        with self._lock:
            self._price     = None
            self._timestamp = 0.0

    def peek(self) -> Optional[float]:
        """Return the cached price without triggering a fetch (None if empty)."""
        with self._lock:
            return self._price

    def stats(self) -> dict:
        """Return cache hit/miss telemetry."""
        with self._lock:
            total = self._hits + self._misses
            ratio = self._hits / total if total > 0 else 0.0
            return {
                "hits":       self._hits,
                "misses":     self._misses,
                "hit_ratio":  round(ratio, 4),
                "cached_price": self._price,
                "ttl_seconds":  self._ttl,
            }

    def set_ttl(self, ttl: float) -> None:
        """Adjust TTL at runtime (e.g. tighter during high-volatility periods)."""
        with self._lock:
            self._ttl = ttl


# ── Module-level singleton ────────────────────────────────────────────────────
price_cache = _PriceCache()
