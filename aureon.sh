#!/bin/bash

SERVER_LOG="aureon.log"
PID_FILE="aureon.pid"

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null; then
            echo "Server is already running. PID: $PID"
            exit 1
        else
            echo "Removing stale PID file."
            rm -f "$PID_FILE"
        fi
    fi
    echo "Starting Aureon server..."
    nohup python3 aureon_server.py > "$SERVER_LOG" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    PID=$(cat "$PID_FILE")
    if ps -p $PID > /dev/null; then
        echo "Server started with PID $PID"
    else
        echo "Failed to start server. Check $SERVER_LOG for errors."
        rm -f "$PID_FILE"
    fi
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "Server is not running."
        exit 1
    fi
    PID=$(cat "$PID_FILE")
    if ps -p $PID > /dev/null; then
        echo "Stopping Aureon server with PID $PID..."
        kill $PID
        sleep 1
        echo "Server stopped."
    else
        echo "Process $PID not running, removing PID file."
    fi
    rm -f "$PID_FILE"
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null; then
            echo "Server running. PID: $PID"
        else
            echo "Server not running, removing stale PID file."
            rm -f "$PID_FILE"
        fi
    else
        echo "Server is not running."
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        stop
        start
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
esac

