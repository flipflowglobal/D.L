#!/bin/bash
# ==============================================================================
# start_system.sh — AUREON Consolidated System Entrypoint
# ==============================================================================
#
# Replaces fragmented start scripts (start_aureon.sh, start_aureon_full.sh)
# with a single, controlled entrypoint that:
#
#   1. Activates the Python environment (venv or system)
#   2. Installs / verifies dependencies
#   3. Runs integrity checks
#   4. Starts the WatchdogKernel (which manages all subsystems)
#
# The kernel handles:
#   - API server lifecycle (auto-restart on crash)
#   - Agent-level watchdog monitoring
#   - Periodic state snapshots
#   - Graceful shutdown on SIGTERM/SIGINT
#
# Usage:
#   ./start_system.sh              # start in foreground
#   ./start_system.sh --daemon     # start in background (nohup)
#   ./start_system.sh --check      # run integrity check only
#
# Environment variables (all optional):
#   API_HOST                  — bind address (default: 0.0.0.0)
#   API_PORT                  — bind port (default: 8010)
#   KERNEL_HEALTH_INTERVAL    — health check interval in seconds (default: 10)
#   KERNEL_SNAPSHOT_INTERVAL  — snapshot interval in seconds (default: 60)
#   KERNEL_MAX_RESTARTS       — max API restarts before degraded (default: 10)
#   KERNEL_INTEGRITY_CHECK    — run integrity check on boot (default: true)
#
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_DIR="${SCRIPT_DIR}/logs"
KERNEL_LOG="${LOG_DIR}/kernel.log"
PID_FILE="${SCRIPT_DIR}/kernel.pid"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Ensure log directory ──────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Parse arguments ───────────────────────────────────────────────────────────
MODE="foreground"
for arg in "$@"; do
    case "$arg" in
        --daemon)  MODE="daemon"  ;;
        --check)   MODE="check"   ;;
        --help|-h)
            echo "Usage: $0 [--daemon|--check|--help]"
            echo "  --daemon  Run kernel in background"
            echo "  --check   Run integrity check only"
            exit 0
            ;;
    esac
done

# ── Python environment ────────────────────────────────────────────────────────
activate_venv() {
    if [ -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "${SCRIPT_DIR}/venv/bin/activate"
        log_info "Virtual environment activated"
    elif [ -f "$HOME/OnTheDL/venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "$HOME/OnTheDL/venv/bin/activate"
        log_info "Virtual environment activated (home)"
    else
        log_warn "No virtualenv found — using system Python"
    fi
}

activate_venv

# ── Dependency check ──────────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
    log_info "Verifying dependencies…"
    pip install -q -r "${SCRIPT_DIR}/requirements.txt" 2>/dev/null || \
        log_warn "Some dependencies may be missing"
fi

# ── Integrity check mode ─────────────────────────────────────────────────────
if [ "$MODE" = "check" ]; then
    log_info "Running integrity check…"
    python -c "
from core.integrity import IntegrityChecker
checker = IntegrityChecker()
result = checker.verify_all()
import json
print(json.dumps(result, indent=2))
exit(0 if result['passed'] else 1)
"
    exit $?
fi

# ── Stop existing kernel ─────────────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        log_warn "Stopping existing kernel (PID=$OLD_PID)…"
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
    fi
    rm -f "$PID_FILE"
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "======================================"
echo "  AUREON Autonomous Runtime System"
echo "  Kernel + Watchdog + State Recovery"
echo "======================================"
echo ""

# ── Start kernel ─────────────────────────────────────────────────────────────
if [ "$MODE" = "daemon" ]; then
    log_info "Starting WatchdogKernel in daemon mode…"
    nohup python -m kernel.watchdog_kernel > "$KERNEL_LOG" 2>&1 &
    KERNEL_PID=$!
    echo "$KERNEL_PID" > "$PID_FILE"
    log_info "Kernel started (PID=$KERNEL_PID)"
    log_info "Logs: tail -f $KERNEL_LOG"
    log_info "Stop:  kill \$(cat $PID_FILE)"
else
    log_info "Starting WatchdogKernel in foreground…"
    log_info "Press Ctrl+C to stop"
    echo ""
    python -m kernel.watchdog_kernel
fi
