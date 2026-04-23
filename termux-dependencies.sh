#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[TERMUX-DEPS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

if ! command -v pkg >/dev/null 2>&1; then
  fail "This script must be run in Termux (pkg command not found)."
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

info "Updating Termux packages..."
pkg update -y
pkg upgrade -y

info "Installing required system dependencies..."
pkg install -y \
  git curl wget \
  python rust \
  clang make cmake pkg-config \
  libffi openssl

info "Installing optional build/runtime helpers (best effort)..."
OPTIONAL_PKGS=(
  libjpeg-turbo
  zlib
  sqlite
)
for p in "${OPTIONAL_PKGS[@]}"; do
  if ! pkg install -y "$p" >/dev/null 2>&1; then
    warn "Optional package '$p' is unavailable; continuing."
  fi
done

PYTHON_BIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON_BIN" ]] || fail "Python not found after package install."

info "Upgrading pip/setuptools/wheel..."
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel

info "Installing core Python scientific/build dependencies..."
"$PYTHON_BIN" -m pip install "numpy>=1.24.0" "cython>=3.0.0"

if [[ -f requirements-termux.txt ]]; then
  info "Installing Termux Python requirements..."
  "$PYTHON_BIN" -m pip install -r requirements-termux.txt
elif [[ -f requirements.txt ]]; then
  warn "requirements-termux.txt not found; falling back to requirements.txt"
  "$PYTHON_BIN" -m pip install -r requirements.txt
else
  fail "No requirements file found."
fi

if [[ -f requirements-optional.txt ]]; then
  info "Installing Termux-safe optional dependencies (excluding playwright)..."
  while IFS= read -r dep; do
    [[ -z "$dep" ]] && continue
    "$PYTHON_BIN" -m pip install "$dep" || warn "Could not install optional dep: $dep"
  done < <(awk 'NF && $1 !~ /^#/ {print $1}' requirements-optional.txt | grep -vi '^playwright' || true)
fi

info "Verifying critical imports..."
"$PYTHON_BIN" - <<'PY'
mods = [
    "dotenv", "requests", "httpx", "numpy",
    "web3", "eth_account", "fastapi", "uvicorn", "pydantic", "aiosqlite"
]
failed = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        failed.append((m, str(e)))
if failed:
    for name, err in failed:
        print(f"[MISSING] {name}: {err}")
    raise SystemExit(1)
print("All critical imports succeeded.")
PY

info "Dependency installation complete."
echo "Run next: bash /home/runner/work/D.L/D.L/termux-setup.sh"
