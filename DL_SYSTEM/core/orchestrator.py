from agents.task_agent import TaskAgent
from core.state_manager import StateManager
from core.logger import log_event

class Orchestrator:
    def __init__(self):
        self.state = StateManager()
        self.task_agent = TaskAgent()

    def run_cycle(self):
        print("[*] Starting execution cycle")

        tasks = self.state.get_tasks()

        for task in tasks:
            print(f"[+] Executing: {task['name']}")
            result = self.task_agent.execute(task)

            log_event({
                "task": task["name"],
                "result": result
            })

            self.state.update_task(task["id"], result)

        print("[✓] Cycle complete\n")
