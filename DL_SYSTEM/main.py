import sys
import os
import time

# Ensure DL_SYSTEM/ is always on the path regardless of how this is invoked
sys.path.insert(0, os.path.dirname(__file__))

from core.orchestrator import Orchestrator

if __name__ == "__main__":
    orchestrator = Orchestrator()

    while True:
        orchestrator.run_cycle()
        time.sleep(600)
