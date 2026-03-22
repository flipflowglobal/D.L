import json
import os

STATE_FILE = "DL_SYSTEM/data/state.json"

class StateManager:
    def __init__(self):
        os.makedirs("DL_SYSTEM/data", exist_ok=True)
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                json.dump({"tasks": []}, f)

    def load(self):
        with open(STATE_FILE) as f:
            return json.load(f)

    def save(self, data):
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=4)

    def get_tasks(self):
        return self.load()["tasks"]

    def update_task(self, task_id, result):
        data = self.load()
        for t in data["tasks"]:
            if t["id"] == task_id:
                t["last_result"] = result
        self.save(data)
