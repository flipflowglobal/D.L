"""
DL_SYSTEM/core/config_loader.py — Environment variable loader for DL_SYSTEM.

All DL_SYSTEM components import `Config` from here rather than reading
os.getenv() directly, keeping credential access centralised.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Centralised config for DL_SYSTEM agents and integrations."""

    # ── Blockchain ────────────────────────────────────────────────────────────
    RPC_URL:        Optional[str] = os.getenv("RPC_URL") or os.getenv("ETH_RPC")
    PRIVATE_KEY:    Optional[str] = os.getenv("PRIVATE_KEY")
    WALLET_ADDRESS: Optional[str] = os.getenv("WALLET_ADDRESS")

    # ── Quest platform credentials ────────────────────────────────────────────
    GALXE_EMAIL:      Optional[str] = os.getenv("GALXE_EMAIL")
    GALXE_PASSWORD:   Optional[str] = os.getenv("GALXE_PASSWORD")
    LAYER3_EMAIL:     Optional[str] = os.getenv("LAYER3_EMAIL")
    LAYER3_PASSWORD:  Optional[str] = os.getenv("LAYER3_PASSWORD")

    # ── Orchestration ─────────────────────────────────────────────────────────
    CYCLE_INTERVAL: int = int(os.getenv("DL_CYCLE_INTERVAL", "600"))
