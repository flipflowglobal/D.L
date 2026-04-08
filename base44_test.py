#!/usr/bin/env python3
"""
base44_test.py — Test connectivity to the Base44 agent API.
Requires BASE44_API_KEY and optionally BASE44_API_URL in .env
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv(".env")

BASE44_API_KEY = os.getenv("BASE44_API_KEY", "")
BASE44_API_URL = os.getenv(
    "BASE44_API_URL",
    "https://preview-sandbox--69b04a5bf0be8d0ff013b05a.base44.app"
)
AGENT_ID = os.getenv("BASE44_AGENT_ID", "aureon")

if not BASE44_API_KEY:
    raise RuntimeError(
        "BASE44_API_KEY not set. Add it to .env:\n"
        "  BASE44_API_KEY=your_key_here"
    )

headers = {
    "Authorization": f"Bearer {BASE44_API_KEY}",
    "Content-Type": "application/json",
}

print("=== TESTING BASE44 CONNECTION ===")

r = requests.get(BASE44_API_URL, timeout=10)
print("Connection status:", r.status_code)

print("\n=== CREATING SOLANA WALLET ===")

payload = {
    "agent_id": AGENT_ID,
    "task_type": "blockchain",
    "payload": {"command": "new wallet solana"},
}

try:
    r = requests.post(
        f"{BASE44_API_URL}/api/tasks",
        headers=headers,
        json=payload,
        timeout=15,
    )
    print("Status:", r.status_code)
    print("Raw response:", r.text)
except Exception as e:
    print({"error": str(e)})

print("\n=== LISTING ALL WALLETS ===")

try:
    r = requests.get(
        f"{BASE44_API_URL}/api/wallets?agent_id={AGENT_ID}",
        headers=headers,
        timeout=15,
    )
    print("Status:", r.status_code)
    print("Raw response:", r.text)
except Exception as e:
    print({"error": str(e)})
