#!/usr/bin/env python3
"""
setup_cython.py — Cython compilation build script for AUREON hot-path modules.

Usage:
    python setup_cython.py build_ext --inplace   # compile .pyx → .so in place
    python setup_cython.py build_ext             # compile into build/ directory

After compilation the .so files sit next to the .py stubs; Python's import
system automatically picks the compiled extension over the pure-Python fallback.

Compiler flags (GCC/Clang):
    -O3            Maximum optimisation
    -march=native  Use all CPU instruction extensions available on this machine
    -ffast-math    Aggressive float optimisation (no NaN/Inf handling overhead)
    -funroll-loops Loop unrolling for tight inner loops
    -fno-wrapv     Assume no signed-integer overflow (enables more opts)
    -DNPY_NO_DEPRECATED_API  Silence NumPy deprecation noise
"""

import os
import sys
from setuptools import setup, Extension

try:
    from Cython.Build import cythonize
    from Cython.Compiler import Options as CythonOptions
except ImportError:
    print("[ERROR] Cython is not installed.  Run: pip install cython")
    sys.exit(1)

# ── Cython compiler directives ────────────────────────────────────────────────
# These apply globally to every .pyx file; individual files may override them
# with their own file-level directives.
DIRECTIVES = {
    "language_level":    "3",
    "boundscheck":       False,   # no index range checking
    "wraparound":        False,   # no negative-index support
    "cdivision":         True,    # C-style integer division (no ZeroDivisionError)
    "nonecheck":         False,   # no None-attribute checks
    "initializedcheck":  False,   # no memoryview initialisation checks
    "overflowcheck":     False,   # no arithmetic overflow checks
    "infer_types":       True,    # infer C types from context
    "emit_code_comments": True,   # embed C-level comments in generated code
    "profile":           False,   # profiling disabled in production
    "linetrace":         False,   # line-tracing disabled
}

# ── Platform compiler flags ───────────────────────────────────────────────────
if sys.platform == "win32":
    # MSVC flags
    _COMPILE = ["/O2", "/fp:fast", "/arch:AVX2"]
    _LINK    = []
else:
    # GCC / Clang flags
    _COMPILE = [
        "-O3",
        "-march=native",
        "-ffast-math",
        "-funroll-loops",
        "-fno-wrapv",
        "-fomit-frame-pointer",
        "-DNPY_NO_DEPRECATED_API=NPY_1_7_API_VERSION",
    ]
    _LINK = ["-O3"]

# ── Extension definitions ─────────────────────────────────────────────────────
extensions = [
    # Portfolio — buy/sell/summary tight loops
    Extension(
        name                 = "engine.portfolio",
        sources              = ["engine/portfolio.pyx"],
        extra_compile_args   = _COMPILE,
        extra_link_args      = _LINK,
        language             = "c",
    ),

    # RiskManager — can_trade() / record_trade() per cycle
    Extension(
        name                 = "engine.risk_manager",
        sources              = ["engine/risk_manager.pyx"],
        extra_compile_args   = _COMPILE,
        extra_link_args      = _LINK,
        language             = "c",
    ),

    # MeanReversionStrategy — C ring-buffer rolling mean
    Extension(
        name                 = "engine.strategies.mean_reversion",
        sources              = ["engine/strategies/mean_reversion.pyx"],
        extra_compile_args   = _COMPILE,
        extra_link_args      = _LINK,
        language             = "c",
    ),
]

# ── Generate annotated HTML reports ──────────────────────────────────────────
# Creates engine/*.html files showing which lines were compiled to C vs Python.
# Useful for profiling Cython coverage.
CythonOptions.annotate = True

# ── Build ─────────────────────────────────────────────────────────────────────
setup(
    name        = "aureon_engine",
    version     = "1.0.0",
    description = "AUREON compiled trading engine extensions",
    ext_modules = cythonize(
        extensions,
        compiler_directives = DIRECTIVES,
        annotate            = True,
        nthreads            = os.cpu_count() or 1,   # parallel compilation
        force               = False,                  # skip unchanged files
        quiet               = False,
    ),
    zip_safe = False,
)
