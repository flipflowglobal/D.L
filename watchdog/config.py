"""
watchdog/config.py — Centralized threshold and timing configuration.

All watchdog agents and healing strategies read their tunable parameters
from this module.  Values can be overridden via environment variables so
that production deployments never need code changes.
"""

from __future__ import annotations

import os


def _float(key: str, default: float) -> float:
    return float(os.getenv(key, default))


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, default))


# ── Poll intervals (seconds) ──────────────────────────────────────────────────

FILE_AGENT_INTERVAL     = _float("WDOG_FILE_INTERVAL",     15.0)
PROCESS_AGENT_INTERVAL  = _float("WDOG_PROCESS_INTERVAL",  10.0)
SERVICE_AGENT_INTERVAL  = _float("WDOG_SERVICE_INTERVAL",  10.0)
DB_AGENT_INTERVAL       = _float("WDOG_DB_INTERVAL",       60.0)
RESOURCE_AGENT_INTERVAL = _float("WDOG_RESOURCE_INTERVAL", 30.0)
TRADE_AGENT_INTERVAL    = _float("WDOG_TRADE_INTERVAL",    20.0)

# ── Healing strategy ──────────────────────────────────────────────────────────

HEAL_COOLDOWN_SEC    = _float("WDOG_HEAL_COOLDOWN",    30.0)   # min gap between heals
HEAL_MAX_ATTEMPTS    = _int("WDOG_HEAL_MAX_ATTEMPTS",  10)     # max heals per window
HEAL_WINDOW_SEC      = _float("WDOG_HEAL_WINDOW",      600.0)  # rolling window for max
CONSENSUS_QUORUM     = _float("WDOG_CONSENSUS_QUORUM", 0.51)   # 51% quorum

# ── Resource agent thresholds ─────────────────────────────────────────────────

CPU_WARN_PCT         = _float("WDOG_CPU_WARN",   85.0)
CPU_CRIT_PCT         = _float("WDOG_CPU_CRIT",   95.0)
MEM_WARN_PCT         = _float("WDOG_MEM_WARN",   80.0)
MEM_CRIT_PCT         = _float("WDOG_MEM_CRIT",   92.0)
DISK_WARN_PCT        = _float("WDOG_DISK_WARN",  80.0)
DISK_CRIT_PCT        = _float("WDOG_DISK_CRIT",  90.0)
FD_WARN              = _int("WDOG_FD_WARN",      800)
FD_CRIT              = _int("WDOG_FD_CRIT",      1000)

# ── Process / service agent ───────────────────────────────────────────────────

PROCESS_HTTP_TIMEOUT  = _float("WDOG_PROC_HTTP_TIMEOUT",  0.5)   # seconds
PROCESS_LATENCY_WARN  = _float("WDOG_PROC_LATENCY_WARN",  0.2)   # seconds
PROCESS_MAX_RESTARTS  = _int("WDOG_PROC_MAX_RESTARTS",    5)

SERVICE_HTTP_TIMEOUT  = _float("WDOG_SVC_HTTP_TIMEOUT",   2.0)
SERVICE_LATENCY_WARN  = _float("WDOG_SVC_LATENCY_WARN",   0.5)

# ── DB agent ──────────────────────────────────────────────────────────────────

DB_SIZE_WARN_MB      = _float("WDOG_DB_SIZE_WARN_MB", 50.0)

# ── Trade loop agent ──────────────────────────────────────────────────────────

TRADE_STALE_THRESHOLD = _float("WDOG_TRADE_STALE_SEC", 120.0)  # seconds without cycle

# ── Event bus ─────────────────────────────────────────────────────────────────

EVENT_BUS_QUEUE_SIZE  = _int("WDOG_EVENT_QUEUE_SIZE", 1000)

# ── Dashboard ─────────────────────────────────────────────────────────────────

DASHBOARD_MAX_EVENTS  = _int("WDOG_DASHBOARD_MAX_EVENTS", 500)
DASHBOARD_PORT        = _int("WDOG_DASHBOARD_PORT",        8020)

# ── Sidecar ports ─────────────────────────────────────────────────────────────

DEX_ORACLE_PORT       = _int("DEX_ORACLE_PORT", 9001)
TX_ENGINE_PORT        = _int("TX_ENGINE_PORT",  9002)
