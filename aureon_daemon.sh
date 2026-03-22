#!/bin/bash
# ------------------------------------------------------
# AUREON + ONTHEDL DAEMON START SCRIPT
# Fully detached background server with logging
# ------------------------------------------------------

# 1️⃣ Activate virtual environment
echo "Activating virtual environment..."
source ~/OnTheDL/venv/bin/activate

# 2️⃣ Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# 3️⃣ Install required dependencies
echo "Installing required Python packages..."
pip install --upgrade fastapi uvicorn[standard] aiosqlite pydantic web3 solders requests

# 4️⃣ Kill any process using port 8010
echo "Checking for existing processes on port 8010..."
PID=$(lsof -ti:8010)
if [ ! -z "$PID" ]; then
    echo "Killing process $PID on port 8010..."
    kill -9 $PID
else
    echo "No process found on port 8010."
fi

# 5️⃣ Rotate log files
LOGFILE="aureon_daemon.log"
if [ -f $LOGFILE ]; then
    mv $LOGFILE "${LOGFILE}.bak.$(date +%Y%m%d%H%M%S)"
fi

# 6️⃣ Start server detached (nohup)
echo "Starting Aureon + OnTheDL server as a daemon..."
nohup uvicorn main:app --host 0.0.0.0 --port 8010 > $LOGFILE 2>&1 &
SERVER_PID=$!
echo "Server started in background with PID: $SERVER_PID"

# 7️⃣ Optional: Wait a few seconds and verify startup
sleep 5
if ps -p $SERVER_PID > /dev/null; then
   echo "Server is running successfully."
else
   echo "Server failed to start. Check $LOGFILE for errors."
fi

# 8️⃣ Test Base44 connection
echo "Testing Base44 API connection..."
python3 - <<EOF
import requests, json

BASE44_API_KEY = "ba6a4c79de1847d0ad9aaec5eeac9b01"
BASE44_API_URL = "https://api.base44.com"
AGENT_ID = "YOUR_AGENT_ID"  # Replace with your Aureon + OnTheDL agent ID

headers = {
    "Authorization": f"Bearer {BASE44_API_KEY}",
    "Content-Type": "application/json"
}

try:
    response = requests.get(f"{BASE44_API_URL}/ping", headers=headers)
    print("Base44 connection status:", response.status_code)
except Exception as e:
    print("Error connecting to Base44:", e)
EOF

echo "Daemon startup script completed. Logs at $LOGFILE"
echo "Use 'tail -f $LOGFILE' to watch server output."
