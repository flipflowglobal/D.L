"""
DL_SYSTEM/integrations/galxe.py — Galxe quest automation.

Navigates to Galxe, logs in with credentials from Config, and visits
the quests page.  Requires playwright to be installed.
"""

from __future__ import annotations

import time
from typing import Any

from agents.web_agent_v2 import WebAgent
from core.config_loader import Config


def run_galxe_task(task: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a Galxe quest task using a headless browser.

    Args:
        task: task dict from StateManager (must have ``type == "galxe"``).

    Returns:
        Result dict with ``status`` key.
    """
    email    = Config.GALXE_EMAIL
    password = Config.GALXE_PASSWORD

    if not email or not password:
        return {
            "status": "error",
            "error":  "GALXE_EMAIL or GALXE_PASSWORD not set in .env",
        }

    agent = WebAgent()
    try:
        agent.goto("https://galxe.com")
        time.sleep(2)

        agent.login_generic(email, password)
        time.sleep(3)

        agent.goto("https://galxe.com/quests")
        time.sleep(3)

        return {"status": "executed", "platform": "galxe"}

    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    finally:
        agent.close()
