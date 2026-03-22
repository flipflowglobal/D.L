#!/bin/bash
# -----------------------------
# Start Aureon + OnTheDL Full Stack
# -----------------------------

# 1️⃣ Set environment variables
export PATH=$HOME/.local/bin:$PATH
VENV_DIR="$HOME/OnTheDL/venv"
BASE44_TEST="$HOME/OnTheDL/base44_test.py"
LOG_DIR="$HOME/OnTheDL/logs"
SERVER_LOG="$LOG_DIR/aureon_server.log"

# 2️⃣ Ensure logs folder exists
mkdir -p "$LOG_DIR"

# 3️⃣ Activate virtual environment
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "[INFO] Virtualenv not found, creating..."
    python3 -m virtualenv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
fi

# 4️⃣ Upgrade pip & install requirements
pip install --upgrade pip setuptools wheel
REQ_FILE="$HOME/OnTheDL/requirements.txt"
if [ -f "$REQ_FILE" ]; then
    pip install -r "$REQ_FILE"
else
    echo "[WARN] requirements.txt not found, installing core dependencies..."
    pip install fastapi uvicorn aiosqlite pydantic web3 solders requests
fi

# 5️⃣ Clean any stray files that may cause issues
rm -f "$HOME/OnTheDL/agent_type:" "$HOME/OnTheDL/name:" "$HOME/OnTheDL/'}'"

# 6️⃣ Kill any existing server process on port 8010
pkill -f uvicorn
pkill -f python

# 7️⃣ Start Aureon server in background
echo "[INFO] Starting Aureon server..."
nohup uvicorn main:app --host 0.0.0.0 --port 8010 > "$SERVER_LOG" 2>&1 &

sleep 2
echo "[INFO] Server started. PID: $(pgrep -f 'uvicorn main:app')"

# 8️⃣ Test Base44 API connection
if [ -f "$BASE44_TEST" ]; then
    echo "[INFO] Testing Base44 API..."
    python3 "$BASE44_TEST"
else
    echo "[WARN] Base44 test script not found, skipping..."
fi

echo "[INFO] Aureon + OnTheDL full stack initialization complete!"
