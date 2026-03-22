#!/bin/bash

echo "======================================"
echo " Starting AUREON Cognitive System"
echo "======================================"

cd ~/OnTheDL

echo "[1] Activating Python environment..."
source venv/bin/activate

echo "[2] Starting FastAPI server in background..."

nohup uvicorn main:app --host 0.0.0.0 --port 8010 > aureon.log 2>&1 &

SERVER_PID=$!

echo "[3] Server PID: $SERVER_PID"

sleep 3

echo "[4] Starting cognitive agent..."

curl -X POST "http://localhost:8010/aureon/start?agent_id=AUREON"

echo ""
echo "AUREON SYSTEM RUNNING"
echo "Logs: tail -f ~/OnTheDL/aureon.log"
echo "Stop system: ./stop_aureon.sh"
