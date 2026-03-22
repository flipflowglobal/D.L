#!/bin/bash

# Path to Python script
SCRIPT="aureon_onthedl.py"
# PID file
PIDFILE="aureon_server.pid"

start() {
    if [ -f $PIDFILE ] && kill -0 $(cat $PIDFILE) 2>/dev/null; then
        echo "Server is already running."
        exit 1
    fi
    echo "Starting Aureon + OnTheDL server..."
    nohup python3 $SCRIPT > aureon_server.log 2>&1 &
    echo $! > $PIDFILE
    echo "Server started with PID $(cat $PIDFILE)"
}

stop() {
    if [ -f $PIDFILE ] && kill -0 $(cat $PIDFILE) 2>/dev/null; then
        echo "Stopping server with PID $(cat $PIDFILE)..."
        kill $(cat $PIDFILE)
        rm -f $PIDFILE
        echo "Server stopped."
    else
        echo "Server is not running."
    fi
}

restart() {
    stop
    sleep 1
    start
}

status() {
    if [ -f $PIDFILE ] && kill -0 $(cat $PIDFILE) 2>/dev/null; then
        echo "Server is running with PID $(cat $PIDFILE)."
    else
        echo "Server is not running."
    fi
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    status) status ;;
    *) echo "Usage: $0 {start|stop|restart|status}" ;;
esac
