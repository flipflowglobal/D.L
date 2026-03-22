from core.orchestrator import Orchestrator
import time

if __name__ == "__main__":
    orchestrator = Orchestrator()

    while True:
        orchestrator.run_cycle()
        time.sleep(600)
