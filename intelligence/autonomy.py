import asyncio


class AgentLoop:
    def __init__(self):
        self.running = False

    async def run(self, agent_id: str):
        print(f"[AUREON] Agent {agent_id} started")
        while self.running:
            print(f"[AUREON] Agent {agent_id} cycle running...")
            await asyncio.sleep(60)
        print(f"[AUREON] Agent {agent_id} stopped")


loop = AgentLoop()
