#!/bin/bash
# ----------------- Aureon + OnTheDL Server Control -----------------
SCRIPT="$HOME/OnTheDL/Aureon_onthedl.py"
PID_FILE="$HOME/OnTheDL/aureon_server.pid"
LOG_FILE="$HOME/OnTheDL/aureon.log"

start() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "Server already running with PID $(cat $PID_FILE)"
        exit 1
    fi
    echo "Starting Aureon + OnTheDL server..."
    nohup python3 "$SCRIPT" > "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"
    echo "Server started with PID $PID"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 $PID 2>/dev/null; then
            echo "Stopping server PID $PID"
            kill -9 $PID
            rm -f "$PID_FILE"
            echo "Server stopped"
        else
            echo "Server PID file exists but process not running"
            rm -f "$PID_FILE"
        fi
    else
        echo "Server not running"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "Server running with PID $(cat $PID_FILE)"
    else
        echo "Server not running"
    fi
}

logs() {
    tail -f "$LOG_FILE"
}

restart() {
    stop
    sleep 1
    start
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    status) status ;;
    logs) logs ;;
    *) echo "Usage: $0 {start|stop|restart|status|logs}" ;;
esac
exit 0
