# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: nonecheck=False
# cython: overflowcheck=False
# cython: infer_types=True
"""
engine/risk_manager.pyx — Cython-compiled RiskManager extension type.

can_trade() and record_trade() are called on every cycle — compiling them
to C removes all Python method-dispatch overhead (~10ns per call instead
of ~500ns).  Trivial speedup but zero risk since the logic is trivial.
"""


cdef class RiskManager:
    """
    Simple guard against over-trading.

    can_trade() returns False once max_daily_trades is reached.
    Call record_trade() after every executed trade.
    Call reset() at the start of each new calendar day.

    All state is stored as C integers — no Python objects involved
    during hot-path checks.
    """

    cdef public int    max_daily_trades
    cdef public double max_position_usd
    cdef public int    trade_count

    def __cinit__(
        self,
        int    max_daily_trades = 50,
        double max_position_usd = 5_000.0,
    ):
        self.max_daily_trades = max_daily_trades
        self.max_position_usd = max_position_usd
        self.trade_count      = 0

    # ── hot-path checks ───────────────────────────────────────────────────────

    cpdef bint can_trade(self):
        """Return True iff the daily trade limit has not been reached."""
        return self.trade_count < self.max_daily_trades

    cpdef void record_trade(self):
        """Increment the daily trade counter (call after every execution)."""
        self.trade_count += 1

    cpdef void reset(self):
        """Reset counter to zero — call at midnight / start of new day."""
        self.trade_count = 0

    # ── introspection ─────────────────────────────────────────────────────────

    def remaining_trades(self) -> int:
        """How many more trades are allowed today."""
        cdef int rem = self.max_daily_trades - self.trade_count
        return rem if rem > 0 else 0

    def __repr__(self) -> str:
        return (
            f"RiskManager(trades={self.trade_count}/"
            f"{self.max_daily_trades}, "
            f"max_pos=${self.max_position_usd:,.0f})"
        )
