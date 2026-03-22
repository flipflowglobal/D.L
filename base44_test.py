import requests
import json

print("=== TESTING BASE44 CONNECTION ===")

BASE44_API_KEY = "ba6a4c79de1847d0ad9aaec5eeac9b01"
BASE44_API_URL = "https://preview-sandbox--69b04a5bf0be8d0ff013b05a.base44.app"
AGENT_ID = "aureon"

headers = {
    "Authorization": f"Bearer {BASE44_API_KEY}",
    "Content-Type": "application/json"
}

# ---------------------
# Connection test
# ---------------------

r = requests.get(BASE44_API_URL)
print("Connection status:", r.status_code)

print("\n=== CREATING SOLANA WALLET ===")

payload = {
    "agent_id": AGENT_ID,
    "task_type": "blockchain",
    "payload": {
        "command": "new wallet solana"
    }
}

try:
    r = requests.post(
        f"{BASE44_API_URL}/api/tasks",
        headers=headers,
        json=payload
    )

    print("Status:", r.status_code)
    print("Raw response:", r.text)

except Exception as e:
    print({"error": str(e)})

print("\n=== LISTING ALL WALLETS ===")

try:
    r = requests.get(
        f"{BASE44_API_URL}/api/wallets?agent_id={AGENT_ID}",
        headers=headers
    )

    print("Status:", r.status_code)
    print("Raw response:", r.text)

except Exception as e:
    print({"error": str(e)})
