"""
DL_SYSTEM/core/logger.py — JSON event log writer.

Appends structured event records to DL_SYSTEM/logs/logs.json.
Each record has a UTC ISO-8601 timestamp and the event payload.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("dl_system.logger")

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
LOG_FILE = os.path.join(_LOG_DIR, "logs.json")

os.makedirs(_LOG_DIR, exist_ok=True)


def log_event(event: Any) -> None:
    """
    Append *event* as a timestamped record to logs.json.

    Args:
        event: any JSON-serialisable value (dict, list, str, …).
    """
    entry = {
        "time":  datetime.now(timezone.utc).isoformat(),
        "event": event,
    }

    # Load existing log (empty list if file missing or corrupt)
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            data: list = json.load(f)
            if not isinstance(data, list):
                data = []
    except (json.JSONDecodeError, OSError):
        data = []

    data.append(entry)

    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except OSError as exc:
        logger.error("Failed to write log file: %s", exc)
