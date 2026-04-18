import fcntl
import json
import os

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STATE_FILE = os.path.join(_DATA_DIR, "state.json")
_LOCK_FILE = STATE_FILE + ".lock"


class StateManager:
    """
    Manages persistent quest/task state with file locking to prevent
    corruption from concurrent reads/writes.
    """

    def __init__(self):
        os.makedirs(_DATA_DIR, exist_ok=True)
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                json.dump({"tasks": []}, f)

    def load(self):
        with open(STATE_FILE) as f:
            fcntl.flock(f, fcntl.LOCK_SH)  # shared lock for reads
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def save(self, data):
        with open(_LOCK_FILE, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)  # exclusive lock for writes
            try:
                with open(STATE_FILE, "w") as f:
                    json.dump(data, f, indent=4)
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

    def get_tasks(self):
        return self.load()["tasks"]

    def update_task(self, task_id, result):
        with open(_LOCK_FILE, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                for t in data["tasks"]:
                    if t["id"] == task_id:
                        t["last_result"] = result
                with open(STATE_FILE, "w") as f:
                    json.dump(data, f, indent=4)
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
