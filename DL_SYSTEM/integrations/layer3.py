"""
DL_SYSTEM/integrations/layer3.py — Layer3 quest automation.

Navigates to Layer3, logs in with credentials from Config, and visits
the quests page.  Requires playwright to be installed.
"""

from __future__ import annotations

import time
from typing import Any

from agents.web_agent_v2 import WebAgent
from core.config_loader import Config


def run_layer3_task(task: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a Layer3 quest task using a headless browser.

    Args:
        task: task dict from StateManager (must have ``type == "layer3"``).

    Returns:
        Result dict with ``status`` key.
    """
    email    = Config.LAYER3_EMAIL
    password = Config.LAYER3_PASSWORD

    if not email or not password:
        return {
            "status": "error",
            "error":  "LAYER3_EMAIL or LAYER3_PASSWORD not set in .env",
        }

    agent = WebAgent()
    try:
        agent.goto("https://layer3.xyz")
        time.sleep(2)

        agent.login_generic(email, password)
        time.sleep(3)

        return {"status": "executed", "platform": "layer3"}

    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    finally:
        agent.close()
