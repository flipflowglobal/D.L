from integrations.galxe import run_galxe_task
from integrations.layer3 import run_layer3_task

class TaskAgent:
    def execute(self, task):
        if task["type"] == "galxe":
            return run_galxe_task(task)
        elif task["type"] == "layer3":
            return run_layer3_task(task)
        else:
            return {"status": "unknown"}
