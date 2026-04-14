import sys
import os
import asyncio

# Ensure the repo root is on the path so DL_SYSTEM is importable as a package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from DL_SYSTEM.core.orchestrator import Orchestrator


async def main():
    orchestrator = Orchestrator()

    while True:
        await asyncio.to_thread(orchestrator.run_cycle)
        await asyncio.sleep(600)


if __name__ == "__main__":
    asyncio.run(main())
