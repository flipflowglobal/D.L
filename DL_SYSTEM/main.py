"""
DL_SYSTEM/main.py — DL_SYSTEM orchestrator entry point.

Runs the task orchestration loop on a configurable interval.
Handles SIGINT/SIGTERM for graceful shutdown.
"""

from __future__ import annotations

import os
import signal
import sys
import time

# Ensure DL_SYSTEM/ is always on the path regardless of how this is invoked
sys.path.insert(0, os.path.dirname(__file__))

from core.orchestrator import Orchestrator

CYCLE_INTERVAL = int(os.getenv("DL_CYCLE_INTERVAL", "600"))   # seconds between cycles


def main() -> None:
    orchestrator = Orchestrator()
    running = True

    def _shutdown(sig, frame):
        nonlocal running
        print("\n[DL_SYSTEM] Shutdown requested — stopping after current cycle...")
        running = False

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"[DL_SYSTEM] Starting orchestrator (cycle every {CYCLE_INTERVAL}s)")

    while running:
        orchestrator.run_cycle()
        if running:
            time.sleep(CYCLE_INTERVAL)

    print("[DL_SYSTEM] Stopped.")


if __name__ == "__main__":
    main()
