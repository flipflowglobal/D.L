import asyncio
from intelligence.memory import memory


class AgentLoop:
    """
    Autonomous agent loop.  Start with loop.running = True then
    await loop.run(agent_id) as an asyncio Task.
    Stop gracefully by setting loop.running = False.
    """

    CYCLE_INTERVAL = 60  # seconds between cycles

    def __init__(self):
        self.running = False
        self.cycle_count = 0

    async def run(self, agent_id: str):
        print(f"[AUREON] Agent {agent_id} started")
        self.cycle_count = 0

        while self.running:
            self.cycle_count += 1
            print(f"[AUREON] Agent {agent_id} — cycle {self.cycle_count}")

            try:
                await memory.store(agent_id, "last_cycle", str(self.cycle_count))
                await memory.store(agent_id, "status", "running")
            except Exception as e:
                print(f"[AUREON] Memory write error: {e}")

            await asyncio.sleep(self.CYCLE_INTERVAL)

        try:
            await memory.store(agent_id, "status", "stopped")
        except Exception:
            pass

        print(f"[AUREON] Agent {agent_id} stopped after {self.cycle_count} cycles")


loop = AgentLoop()
