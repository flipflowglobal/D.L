#!/usr/bin/env python3
"""
build.py — AUREON master build orchestrator (Phase 5).

Compiles all four language pipelines in parallel then validates the result:

  Pipeline A: Cython  → portfolio.so, risk_manager.so, mean_reversion.so
  Pipeline B: Rust    → dex-oracle (release binary)
  Pipeline C: Rust    → tx-engine  (release binary)
  Pipeline D: Solidity → FlashLoanArbitrage ABI + bytecode (via py-solc-x)

Pipelines A, B, C, D run concurrently via asyncio.gather().
Wall-clock time = max(slowest_pipeline) instead of sum of all.

Usage:
  python build.py              # build everything
  python build.py --cython     # Cython only
  python build.py --rust       # both Rust crates only
  python build.py --sol        # Solidity only
  python build.py --clean      # remove all build artefacts

Output:
  build/
    cython/      .so compiled extensions
    rust/        release binaries (symlinked)
    solidity/    FlashLoanArbitrage.abi  FlashLoanArbitrage.bin
  build/report.json    full build report with timing
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build")

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT     = Path(__file__).parent.resolve()
BUILD    = ROOT / "build"
CYTHON_D = BUILD / "cython"
RUST_D   = BUILD / "rust"
SOL_D    = BUILD / "solidity"

DEX_CRATE = ROOT / "dex-oracle"
TX_CRATE  = ROOT / "tx-engine"
SOL_FILE  = ROOT / "contracts" / "FlashLoanArbitrage.sol"


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline A — Cython
# ─────────────────────────────────────────────────────────────────────────────

async def build_cython() -> dict:
    t0 = time.monotonic()
    log.info("[CYTHON] Starting compilation …")
    setup = ROOT / "setup_cython.py"

    if not setup.exists():
        return _fail("cython", "setup_cython.py not found", t0)

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(setup), "build_ext", "--inplace",
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except Exception as exc:
        return _fail("cython", str(exc), t0)

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[-600:]
        return _fail("cython", f"exit {proc.returncode}\n{err}", t0)

    # Verify .so files exist
    so_files = list(ROOT.glob("engine/**/*.so")) + list(ROOT.glob("engine/*.so"))
    CYTHON_D.mkdir(parents=True, exist_ok=True)
    for so in so_files:
        dest = CYTHON_D / so.name
        shutil.copy2(so, dest)

    elapsed = time.monotonic() - t0
    log.info("[CYTHON] Done in %.1fs — %d .so files", elapsed, len(so_files))
    return _ok("cython", elapsed, {"so_files": [f.name for f in so_files]})


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline B — Rust dex-oracle
# ─────────────────────────────────────────────────────────────────────────────

async def build_rust_dex() -> dict:
    return await _build_rust_crate("dex-oracle", DEX_CRATE)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline C — Rust tx-engine
# ─────────────────────────────────────────────────────────────────────────────

async def build_rust_tx() -> dict:
    return await _build_rust_crate("tx-engine", TX_CRATE)


async def _build_rust_crate(name: str, crate_dir: Path) -> dict:
    t0 = time.monotonic()
    log.info("[RUST/%s] Starting release build …", name)

    if not (crate_dir / "Cargo.toml").exists():
        return _fail(f"rust/{name}", f"{crate_dir}/Cargo.toml not found", t0)

    try:
        proc = await asyncio.create_subprocess_exec(
            "cargo", "build", "--release",
            cwd=str(crate_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return _fail(f"rust/{name}", "cargo not found — install Rust", t0)
    except Exception as exc:
        return _fail(f"rust/{name}", str(exc), t0)

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[-800:]
        return _fail(f"rust/{name}", f"exit {proc.returncode}\n{err}", t0)

    binary = crate_dir / "target" / "release" / name
    if not binary.exists():
        return _fail(f"rust/{name}", "binary not found after build", t0)

    size_mb = binary.stat().st_size / 1_048_576
    RUST_D.mkdir(parents=True, exist_ok=True)
    dest = RUST_D / name
    shutil.copy2(binary, dest)
    dest.chmod(0o755)

    elapsed = time.monotonic() - t0
    log.info("[RUST/%s] Done in %.1fs — %.1f MB", name, elapsed, size_mb)
    return _ok(f"rust/{name}", elapsed, {"binary": str(dest), "size_mb": round(size_mb, 2)})


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline D — Solidity
# ─────────────────────────────────────────────────────────────────────────────

async def build_solidity() -> dict:
    t0 = time.monotonic()
    log.info("[SOLIDITY] Compiling FlashLoanArbitrage.sol …")

    if not SOL_FILE.exists():
        return _fail("solidity", f"{SOL_FILE} not found", t0)

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _compile_solidity_sync)
    except Exception as exc:
        return _fail("solidity", str(exc), t0)

    if result.get("error"):
        return _fail("solidity", result["error"], t0)

    SOL_D.mkdir(parents=True, exist_ok=True)
    abi_path = SOL_D / "FlashLoanArbitrage.abi"
    bin_path = SOL_D / "FlashLoanArbitrage.bin"

    with open(abi_path, "w") as f:
        json.dump(result["abi"], f, indent=2)
    with open(bin_path, "w") as f:
        f.write(result["bytecode"])

    elapsed = time.monotonic() - t0
    log.info("[SOLIDITY] Done in %.1fs — ABI: %d entries, bytecode: %d bytes",
             elapsed, len(result["abi"]), len(result["bytecode"]) // 2)
    return _ok("solidity", elapsed, {
        "abi_path":       str(abi_path),
        "bin_path":       str(bin_path),
        "abi_entries":    len(result["abi"]),
        "bytecode_bytes": len(result["bytecode"]) // 2,
    })


def _compile_solidity_sync() -> dict:
    """Run solcx in a thread (blocking). Installs compiler on first use."""
    try:
        import solcx
    except ImportError:
        return {"error": "py-solc-x not installed — run: pip install py-solc-x"}

    # Install solc 0.8.20 if missing
    installed = solcx.get_installed_solc_versions()
    target    = "0.8.20"
    if not any(str(v) == target for v in installed):
        log.info("[SOLIDITY] Installing solc %s …", target)
        solcx.install_solc(target)

    solcx.set_solc_version(target)

    try:
        output = solcx.compile_source(
            SOL_FILE.read_text(),
            output_values=["abi", "bin"],
            solc_version=target,
            optimize=True,
            optimize_runs=200,
        )
    except Exception as exc:
        return {"error": str(exc)}

    # Find the main contract
    for key, val in output.items():
        if "FlashLoanArbitrage" in key and ":" in key:
            return {"abi": val["abi"], "bytecode": val["bin"]}

    return {"error": "FlashLoanArbitrage contract not found in compiler output"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ok(name: str, elapsed: float, extra: dict = None) -> dict:
    r = {"pipeline": name, "status": "ok", "elapsed_s": round(elapsed, 2)}
    if extra:
        r.update(extra)
    return r


def _fail(name: str, reason: str, t0: float) -> dict:
    elapsed = time.monotonic() - t0
    log.error("[%s] FAILED in %.1fs: %s", name.upper(), elapsed, reason[:200])
    return {"pipeline": name, "status": "error", "elapsed_s": round(elapsed, 2), "error": reason}


def _clean():
    """Remove all build artefacts."""
    removed = 0
    for pat in ["build/", "dex-oracle/target/", "tx-engine/target/"]:
        d = ROOT / pat
        if d.exists():
            shutil.rmtree(d)
            removed += 1
            log.info("Removed %s", d)
    for so in list(ROOT.glob("engine/**/*.so")) + list(ROOT.glob("engine/*.so")):
        so.unlink()
        removed += 1
        log.info("Removed %s", so)
    for c in list(ROOT.glob("engine/**/*.c")) + list(ROOT.glob("engine/*.c")):
        c.unlink()
        removed += 1
    log.info("Clean complete — removed %d paths", removed)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> int:
    if args.clean:
        _clean()
        return 0

    BUILD.mkdir(exist_ok=True)
    wall_t0 = time.monotonic()

    # Select pipelines to run
    pipelines = []
    if args.cython or args.all:
        pipelines.append(build_cython())
    if args.rust or args.all:
        # Run both Rust crates concurrently
        pipelines.append(build_rust_dex())
        pipelines.append(build_rust_tx())
    if args.sol or args.all:
        pipelines.append(build_solidity())

    if not pipelines:
        log.warning("Nothing to build. Pass --all, --cython, --rust, or --sol")
        return 1

    log.info("Running %d pipeline(s) in parallel …", len(pipelines))
    results = await asyncio.gather(*pipelines, return_exceptions=True)

    # Normalize exceptions
    normalized = []
    for r in results:
        if isinstance(r, Exception):
            normalized.append({"pipeline": "unknown", "status": "error", "error": str(r), "elapsed_s": 0})
        else:
            normalized.append(r)

    wall_elapsed = time.monotonic() - wall_t0
    ok_count     = sum(1 for r in normalized if r.get("status") == "ok")
    fail_count   = len(normalized) - ok_count

    report = {
        "wall_clock_s":  round(wall_elapsed, 2),
        "pipelines_ok":  ok_count,
        "pipelines_fail": fail_count,
        "results":       normalized,
    }

    report_path = BUILD / "report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print("\n" + "─" * 60)
    print(f"  AUREON BUILD REPORT  (wall time: {wall_elapsed:.1f}s)")
    print("─" * 60)
    for r in normalized:
        icon = "✓" if r["status"] == "ok" else "✗"
        time_s = f"{r['elapsed_s']:.1f}s"
        err = f"  → {r['error'][:60]}" if r.get("error") else ""
        print(f"  {icon} {r['pipeline']:<22} {time_s:>6}{err}")
    print("─" * 60)
    print(f"  {ok_count}/{len(normalized)} pipelines succeeded")
    print(f"  Report: {report_path}")
    print()

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="AUREON build orchestrator")
    p.add_argument("--all",    action="store_true", default=True,  help="Build all (default)")
    p.add_argument("--cython", action="store_true", help="Cython only")
    p.add_argument("--rust",   action="store_true", help="Rust crates only")
    p.add_argument("--sol",    action="store_true", help="Solidity only")
    p.add_argument("--clean",  action="store_true", help="Remove build artefacts")
    args = p.parse_args()

    # If any specific flag set, disable --all
    if args.cython or args.rust or args.sol or args.clean:
        args.all = False

    sys.exit(asyncio.run(main(args)))
