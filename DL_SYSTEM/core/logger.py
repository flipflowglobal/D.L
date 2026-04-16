import json
from datetime import datetime, timezone
import os

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
LOG_FILE = os.path.join(_LOG_DIR, "logs.json")

os.makedirs(_LOG_DIR, exist_ok=True)


def log_event(event):
    entry = {
        "time": str(datetime.now(timezone.utc)),
        "event": event
    }

    try:
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = []

    data.append(entry)

    with open(LOG_FILE, "w") as f:
        json.dump(data, f, indent=4)
