"""
config.py — Centralised environment-variable loader for all AUREON components.

Import anywhere you need run-time config:

    from config import cfg
    print(cfg.RPC_URL)
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class _Config:
    # ── Ethereum / blockchain ─────────────────────────────────────────────────
    RPC_URL:        Optional[str] = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
    PRIVATE_KEY:    Optional[str] = os.getenv("PRIVATE_KEY")
    WALLET_ADDRESS: Optional[str] = os.getenv("WALLET_ADDRESS")
    PROFIT_WALLET:  Optional[str] = os.getenv("PROFIT_WALLET")
    NETWORK:        str           = os.getenv("NETWORK", "sepolia")   # "mainnet" | "sepolia"

    # ── Alchemy-specific ──────────────────────────────────────────────────────
    # ALCHEMY_API_KEY is an alternative to a full RPC_URL.
    # If set and RPC_URL is empty, RPC_URL is derived automatically.
    ALCHEMY_API_KEY: Optional[str] = os.getenv("ALCHEMY_API_KEY")
    CHAIN_ID:        int           = int(os.getenv("CHAIN_ID", "1"))   # 1 = mainnet

    # ── Transaction confirmation ──────────────────────────────────────────────
    TX_CONFIRM_TIMEOUT: int   = int(os.getenv("TX_CONFIRM_TIMEOUT",  "120"))
    TX_BUMP_TIMEOUT:    int   = int(os.getenv("TX_BUMP_TIMEOUT",     "45"))
    MAX_GAS_LIMIT:      int   = int(os.getenv("MAX_GAS_LIMIT",       "500000"))
    GAS_FEE_BUFFER:     float = float(os.getenv("GAS_FEE_BUFFER_MULTIPLIER", "1.15"))

    # ── Trading parameters ────────────────────────────────────────────────────
    TRADE_SIZE_ETH:   float = float(os.getenv("TRADE_SIZE_ETH",   "0.05"))
    SCAN_INTERVAL:    int   = int(os.getenv("SCAN_INTERVAL",      "30"))
    MIN_PROFIT_USD:   float = float(os.getenv("MIN_PROFIT_USD",   "2.0"))
    GAS_BUDGET_USD:   float = float(os.getenv("GAS_BUDGET_USD",   "5.0"))
    INITIAL_USD:      float = float(os.getenv("INITIAL_USD",      "10000"))
    MAX_DAILY_TRADES: int   = int(os.getenv("MAX_DAILY_TRADES",   "20"))
    MAX_POSITION_USD: float = float(os.getenv("MAX_POSITION_USD", "2000"))

    # ── Strategy ──────────────────────────────────────────────────────────────
    STRATEGY_WINDOW:    int   = int(os.getenv("STRATEGY_WINDOW",    "12"))
    STRATEGY_THRESHOLD: float = float(os.getenv("STRATEGY_THRESHOLD", "0.015"))

    # ── Rust sidecar ports ────────────────────────────────────────────────────
    DEX_ORACLE_PORT: int = int(os.getenv("DEX_ORACLE_PORT", "9001"))
    TX_ENGINE_PORT:  int = int(os.getenv("TX_ENGINE_PORT",  "9002"))

    # ── DL_SYSTEM quest credentials ───────────────────────────────────────────
    GALXE_EMAIL:     Optional[str] = os.getenv("GALXE_EMAIL")
    GALXE_PASSWORD:  Optional[str] = os.getenv("GALXE_PASSWORD")
    LAYER3_EMAIL:    Optional[str] = os.getenv("LAYER3_EMAIL")
    LAYER3_PASSWORD: Optional[str] = os.getenv("LAYER3_PASSWORD")

    # ── Runtime ───────────────────────────────────────────────────────────────
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

    # ── Derived helpers ───────────────────────────────────────────────────────

    def is_live_ready(self) -> bool:
        """Return True if all variables required for live trading are set."""
        rpc = self.RPC_URL or (
            f"https://eth-mainnet.g.alchemy.com/v2/{self.ALCHEMY_API_KEY}"
            if self.ALCHEMY_API_KEY else None
        )
        return bool(rpc and self.PRIVATE_KEY and self.WALLET_ADDRESS)

    def get_rpc_url(self) -> Optional[str]:
        """
        Return the effective RPC URL.

        Priority:
          1. RPC_URL env var (explicit full URL)
          2. ETH_RPC env var (alias)
          3. Derived from ALCHEMY_API_KEY: https://eth-mainnet.g.alchemy.com/v2/KEY
        """
        if self.RPC_URL:
            return self.RPC_URL
        if self.ALCHEMY_API_KEY:
            return f"https://eth-mainnet.g.alchemy.com/v2/{self.ALCHEMY_API_KEY}"
        return None

    def validate_live(self) -> None:
        """Raise ValueError listing every missing variable for live trading."""
        missing = []
        if not self.get_rpc_url():
            missing.append("RPC_URL (or ALCHEMY_API_KEY)")
        if not self.PRIVATE_KEY:
            missing.append("PRIVATE_KEY")
        if not self.WALLET_ADDRESS:
            missing.append("WALLET_ADDRESS")
        if missing:
            raise ValueError(
                f"Live trading requires these .env variables: {', '.join(missing)}\n"
                "Run `python setup_wallet.py` to create a wallet and patch .env.\n"
                "Get a free Alchemy API key at https://www.alchemy.com"
            )

    def validate_deploy(self) -> None:
        """Raise ValueError if variables required for contract deployment are absent."""
        self.validate_live()
        if not self.PROFIT_WALLET:
            raise ValueError(
                "PROFIT_WALLET must be set in .env before deploying flash-loan contracts."
            )


cfg = _Config()
