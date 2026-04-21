import sys
import os
import time
import threading

# Ensure the repo root is on the path so DL_SYSTEM is importable as a package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from DL_SYSTEM.core.orchestrator import Orchestrator


def _run_loop(orchestrator: Orchestrator) -> None:
    """Run orchestrator cycles in a dedicated background thread."""
    while True:
        orchestrator.run_cycle()
        time.sleep(600)


if __name__ == "__main__":
    orchestrator = Orchestrator()
    thread = threading.Thread(target=_run_loop, args=(orchestrator,), daemon=True)
    thread.start()
    thread.join()
