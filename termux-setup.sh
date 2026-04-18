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

# ── 5. Solidity compiler (compiler.py resilient 4-layer chain) ───────────────
# compiler.py supports ARM64 natively with a 4-layer fallback chain:
#   Layer 1: py-solc-x + auto ARM64 binary injection
#   Layer 2: Direct ARM64 solc binary download
#   Layer 3: Remix online API (no binary needed)
#   Layer 4: Embedded verified bytecode (offline, always works)
info "Verifying Solidity compiler support …"
if "$PYTHON" -c "import solcx; print('py-solc-x', solcx.__version__)" 2>/dev/null; then
    info "py-solc-x installed — Layer 1 (ARM64 solc) available"
else
    warn "py-solc-x not installed — compiler.py will use Layers 2–4"
    warn "  Layer 2: Direct ARM64 solc download"
    warn "  Layer 3: Remix online API (requires internet)"
    warn "  Layer 4: Embedded verified bytecode (always works)"
fi
echo
read -rp "  Test compile FlashLoanArbitrage contract? (optional) [y/N] " COMPILE_ANS
if [[ "${COMPILE_ANS,,}" == "y" ]]; then
    info "Compiling FlashLoanArbitrage (compile-only, no deploy) …"
    "$PYTHON" compiler.py --compile-only \
        && info "Solidity compilation succeeded" \
        || warn "Solidity compilation used fallback — check output above"
else
    info "Skipping compile test — run 'python compiler.py --compile-only' anytime"
fi

# ── 6. Optional: compile Cython extensions ───────────────────────────────────
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

# ── 7. Create required directories ────────────────────────────────────────────
mkdir -p vault logs build/solidity DL_SYSTEM/data DL_SYSTEM/logs
info "Directories created"

# ── 8. Set up .env ────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    cp .env.example .env
    warn ".env created from .env.example — edit it with your RPC_URL before running"
else
    info ".env already exists"
fi

# ── 9. Wallet setup ───────────────────────────────────────────────────────────
if [[ ! -f vault/wallet.json ]]; then
    info "No wallet found — running wallet setup …"
    "$PYTHON" setup_wallet.py
else
    ADDR=$("$PYTHON" -c "import json; d=json.load(open('vault/wallet.json')); print(d['address'])" 2>/dev/null || echo "unknown")
    info "Existing wallet: $ADDR"
fi

# ── 10. Run smoke tests ────────────────────────────────────────────────────────
info "Running test suite …"
if "$PYTHON" -m pytest --tb=short -q; then
    info "All tests passed"
else
    warn "Some tests failed — review output above before going live"
fi

# ── 11. Done ──────────────────────────────────────────────────────────────────
echo
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║      Setup complete!                         ║"
echo "  ╚══════════════════════════════════════════════╝"
echo
echo "  Running in Termux (Android) — platform notes:"
echo "    • uvloop disabled     → asyncio fallback (slightly slower, fully functional)"
echo "    • Solidity compiler   → compiler.py supports ARM64 (4-layer fallback chain)"
echo "    • playwright skipped  → DL_SYSTEM quest automation unavailable"
echo "    • Docker unavailable  → use direct Python commands instead"
echo
echo "  Next steps:"
echo "    1. Edit .env  →  set RPC_URL (Alchemy / Infura endpoint)"
echo "    2. Paper trade (safe — no real funds):"
echo "         python trade.py"
echo "    3. Compile & deploy flash loan contract:"
echo "         python compiler.py --compile-only       # compile only"
echo "         python compiler.py                      # compile + deploy"
echo "    4. Run flash loan terminal:"
echo "         python flashloan_terminal.py"
echo "    5. FastAPI server:"
echo "         uvicorn main:app --host 0.0.0.0 --port 8010"
echo
echo "  Full flash loan guide:  TERMUX_FLASHLOAN_GUIDE.md"
echo
