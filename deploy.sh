#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# AUREON — One-Command Bare-Metal Setup
# Usage:  bash deploy.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[AUREON]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║      AUREON  —  Deployment Setup             ║"
echo "  ╚══════════════════════════════════════════════╝"
echo

# ── 1. Check Python ────────────────────────────────────────────────────────

PYTHON=$(command -v python3 || command -v python || die "Python not found. Install Python 3.10+")
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJ=$(echo "$PY_VER" | cut -d. -f1)
PY_MIN=$(echo "$PY_VER" | cut -d. -f2)

if [[ $PY_MAJ -lt 3 || ( $PY_MAJ -eq 3 && $PY_MIN -lt 10 ) ]]; then
    die "Python 3.10+ required (found $PY_VER)"
fi
info "Python $PY_VER found"

# ── 2. Create virtual environment ─────────────────────────────────────────

if [[ ! -d venv ]]; then
    info "Creating virtual environment …"
    "$PYTHON" -m venv venv
fi
source venv/bin/activate
info "Virtual environment active"

# ── 3. Install dependencies ───────────────────────────────────────────────

info "Installing core dependencies …"
pip install --quiet -r requirements.txt

# Install optional extras (psutil, playwright) — skip on Android/Termux
if [[ "$(uname -o 2>/dev/null)" != "Android" ]]; then
    info "Installing optional dependencies (psutil, playwright) …"
    pip install --quiet -r requirements-optional.txt || warn "Optional deps skipped (non-critical)"
else
    warn "Android detected — skipping optional deps (psutil, playwright)"
fi

info "Dependencies installed"

# ── 4. Create required directories ───────────────────────────────────────

mkdir -p vault logs DL_SYSTEM/data DL_SYSTEM/logs
info "Directories created"

# ── 5. Set up .env ────────────────────────────────────────────────────────

if [[ ! -f .env ]]; then
    cp .env.example .env
    warn ".env created from .env.example — edit it before running live"
else
    info ".env already exists"
fi

# ── 6. Wallet setup ───────────────────────────────────────────────────────

if [[ ! -f vault/wallet.json ]]; then
    info "No wallet found — running wallet setup …"
    python setup_wallet.py
else
    ADDR=$(python -c "import json; d=json.load(open('vault/wallet.json')); print(d['address'])" 2>/dev/null || echo "unknown")
    info "Existing wallet: $ADDR"
fi

# ── 7. Run tests ──────────────────────────────────────────────────────────

info "Running test suite …"
if pytest --tb=short -q; then
    info "All tests passed"
else
    warn "Some tests failed — review output above before going live"
fi

# ── 8. Done ───────────────────────────────────────────────────────────────

echo
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║      Setup complete!                         ║"
echo "  ╚══════════════════════════════════════════════╝"
echo
echo "  Next steps:"
echo "    1. Edit .env  →  set RPC_URL to your Alchemy/Infura endpoint"
echo "    2. Paper trade (safe, no real funds):"
echo "         source venv/bin/activate && python trade.py"
echo "    3. Live mainnet trading:"
echo "         python trade.py --live"
echo "    4. FastAPI server:"
echo "         uvicorn main:app --host 0.0.0.0 --port 8010"
echo
