import json
from datetime import datetime
import os

LOG_FILE = "DL_SYSTEM/logs/logs.json"

os.makedirs("DL_SYSTEM/logs", exist_ok=True)


def log_event(event):
    entry = {
        "time": str(datetime.utcnow()),
        "event": event
    }

    try:
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
    except:
        data = []

    data.append(entry)

    with open(LOG_FILE, "w") as f:
        json.dump(data, f, indent=4)
