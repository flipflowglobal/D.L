"""
AUREON System Configuration
─────────────────────────────
Centralised environment-variable loader for all AUREON components.
Import this anywhere you need run-time config:

    from config import cfg
    print(cfg.RPC_URL)
"""

import os
from dotenv import load_dotenv

load_dotenv()


class _Config:
    # ── Ethereum / blockchain ─────────────────────────────────────────────────
    RPC_URL:        str | None = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
    PRIVATE_KEY:    str | None = os.getenv("PRIVATE_KEY")
    WALLET_ADDRESS: str | None = os.getenv("WALLET_ADDRESS")

    # ── Trading parameters ────────────────────────────────────────────────────
    TRADE_SIZE_ETH:    float = float(os.getenv("TRADE_SIZE_ETH",    "0.05"))
    SCAN_INTERVAL:     int   = int(os.getenv("SCAN_INTERVAL",       "30"))
    MIN_PROFIT_USD:    float = float(os.getenv("MIN_PROFIT_USD",    "2.0"))
    GAS_BUDGET_USD:    float = float(os.getenv("GAS_BUDGET_USD",    "5.0"))
    INITIAL_USD:       float = float(os.getenv("INITIAL_USD",       "10000"))
    MAX_DAILY_TRADES:  int   = int(os.getenv("MAX_DAILY_TRADES",    "20"))
    MAX_POSITION_USD:  float = float(os.getenv("MAX_POSITION_USD",  "2000"))

    # ── Strategy ──────────────────────────────────────────────────────────────
    STRATEGY_WINDOW:    int   = int(os.getenv("STRATEGY_WINDOW",    "12"))
    STRATEGY_THRESHOLD: float = float(os.getenv("STRATEGY_THRESHOLD", "0.015"))

    # ── DL_SYSTEM quest credentials ───────────────────────────────────────────
    GALXE_EMAIL:    str | None = os.getenv("GALXE_EMAIL")
    GALXE_PASSWORD: str | None = os.getenv("GALXE_PASSWORD")
    LAYER3_EMAIL:   str | None = os.getenv("LAYER3_EMAIL")
    LAYER3_PASSWORD: str | None = os.getenv("LAYER3_PASSWORD")

    # ── Runtime ───────────────────────────────────────────────────────────────
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

    def is_live_ready(self) -> bool:
        """Return True if all variables required for live trading are set."""
        return bool(self.RPC_URL and self.PRIVATE_KEY and self.WALLET_ADDRESS)

    def validate_live(self) -> None:
        """Raise ValueError listing every missing variable for live trading."""
        missing = []
        if not self.RPC_URL:
            missing.append("RPC_URL")
        if not self.PRIVATE_KEY:
            missing.append("PRIVATE_KEY")
        if not self.WALLET_ADDRESS:
            missing.append("WALLET_ADDRESS")
        if missing:
            raise ValueError(
                f"Live trading requires these .env variables: {', '.join(missing)}\n"
                "Run `python setup_wallet.py` to create a wallet and patch .env."
            )


cfg = _Config()
