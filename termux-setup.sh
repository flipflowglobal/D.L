#!/data/data/com.termux/files/usr/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# AUREON — Termux / Android Setup
# Usage:  bash termux-setup.sh
#
# This script sets up the project for testing on Android Termux.
# It installs only the packages and Python dependencies that work on Android;
# platform-incompatible extras (uvloop, py-solc-x, playwright) are skipped.
# All other platforms (Linux desktop, macOS, Windows/WSL) should use deploy.sh
# or follow the README instead.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[AUREON]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Guard: Termux only ────────────────────────────────────────────────────────
if [[ -z "${TERMUX_VERSION:-}" ]] && ! command -v pkg &>/dev/null; then
    warn "This script is intended for Android Termux."
    warn "On other platforms run:  bash deploy.sh"
    read -rp "Continue anyway? [y/N] " ans
    [[ "${ans,,}" == "y" ]] || exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   AUREON  —  Termux / Android Setup          ║"
echo "  ╚══════════════════════════════════════════════╝"
echo

# ── 1. Install Termux system packages ─────────────────────────────────────────
info "Updating package index …"
pkg update -y

info "Installing required system packages …"
# python   — interpreter
# clang    — C compiler (Cython, native extensions)
# make     — build tool
# libffi   — needed by cffi / web3
# openssl  — TLS for requests / httpx / web3
pkg install -y python clang make libffi openssl

info "System packages ready"

# ── 2. Check Python version ────────────────────────────────────────────────────
PYTHON=$(command -v python3 || command -v python || die "Python not found after pkg install")
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJ=$(echo "$PY_VER" | cut -d. -f1)
PY_MIN=$(echo "$PY_VER" | cut -d. -f2)

if [[ $PY_MAJ -lt 3 || ( $PY_MAJ -eq 3 && $PY_MIN -lt 10 ) ]]; then
    die "Python 3.10+ required (found $PY_VER). Termux may ship an older build; try: pkg install python"
fi
info "Python $PY_VER found"

# ── 3. Upgrade pip ────────────────────────────────────────────────────────────
info "Upgrading pip …"
"$PYTHON" -m pip install --upgrade pip --quiet

# ── 4. Install Python dependencies ───────────────────────────────────────────
info "Installing Python dependencies (Termux profile) …"
# requirements-termux.txt excludes uvloop and py-solc-x which have no Android
# ARM64 support; all other core packages are Android-compatible.
"$PYTHON" -m pip install --quiet -r requirements-termux.txt
info "Python dependencies installed"

# ── 5. Optional: compile Cython extensions ───────────────────────────────────
# The .pyx hot-path modules (portfolio, risk_manager, mean_reversion) fall back
# to pure-Python equivalents when the .so files are absent, so this step is
# optional on Termux.  Uncomment or answer 'y' to compile them.
echo
read -rp "  Compile Cython hot-path modules? (optional, needs ~2 min) [y/N] " CYTHON_ANS
if [[ "${CYTHON_ANS,,}" == "y" ]]; then
    info "Installing Cython …"
    "$PYTHON" -m pip install --quiet "cython>=3.0.0"
    info "Compiling Cython extensions …"
    "$PYTHON" setup_cython.py build_ext --inplace \
        && info "Cython extensions compiled successfully" \
        || warn "Cython compilation failed — pure-Python fallbacks will be used"
else
    info "Skipping Cython compilation (pure-Python fallbacks active)"
fi

# ── 6. Create required directories ────────────────────────────────────────────
mkdir -p vault logs DL_SYSTEM/data DL_SYSTEM/logs
info "Directories created"

# ── 7. Set up .env ────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    cp .env.example .env
    warn ".env created from .env.example — edit it with your RPC_URL before running"
else
    info ".env already exists"
fi

# ── 8. Wallet setup ───────────────────────────────────────────────────────────
if [[ ! -f vault/wallet.json ]]; then
    info "No wallet found — running wallet setup …"
    "$PYTHON" setup_wallet.py
else
    ADDR=$("$PYTHON" -c "import json; d=json.load(open('vault/wallet.json')); print(d['address'])" 2>/dev/null || echo "unknown")
    info "Existing wallet: $ADDR"
fi

# ── 9. Run smoke tests ────────────────────────────────────────────────────────
info "Running test suite …"
if "$PYTHON" -m pytest --tb=short -q; then
    info "All tests passed"
else
    warn "Some tests failed — review output above before going live"
fi

# ── 10. Done ──────────────────────────────────────────────────────────────────
echo
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║      Setup complete!                         ║"
echo "  ╚══════════════════════════════════════════════╝"
echo
echo "  Running in Termux (Android) — active limitations:"
echo "    • uvloop disabled     → asyncio fallback (slightly slower, fully functional)"
echo "    • py-solc-x skipped   → Solidity compilation unavailable"
echo "    • playwright skipped  → DL_SYSTEM quest automation unavailable"
echo "    • Docker unavailable  → use direct Python commands instead"
echo
echo "  Next steps:"
echo "    1. Edit .env  →  set RPC_URL (Alchemy / Infura endpoint)"
echo "    2. Paper trade (safe — no real funds):"
echo "         python trade.py"
echo "    3. FastAPI server:"
echo "         uvicorn main:app --host 0.0.0.0 --port 8010"
echo
