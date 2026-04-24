"""
hotswap/loader.py — Safe module-loading helpers for hot-swappable extensions.

Use ``safe_import(module_name)`` instead of a bare ``import`` when you need
a module that may be hot-reloaded at runtime.  The loader always returns the
*latest* version of the module from sys.modules, so callers naturally pick up
fresh code without holding stale references.

Example
-------
    from hotswap.loader import safe_import, extension_available

    if extension_available("engine.portfolio"):
        port_mod = safe_import("engine.portfolio")
        Portfolio = port_mod.Portfolio
    else:
        from engine.portfolio_py import Portfolio   # pure-Python fallback
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from typing import Any

logger = logging.getLogger("hotswap.loader")


def extension_available(module_name: str) -> bool:
    """Return True if a compiled extension (.so / .pyd) exists for *module_name*."""
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return False
    origin = spec.origin or ""
    return origin.endswith((".so", ".pyd"))


def safe_import(module_name: str) -> Any:
    """
    Import *module_name*, always returning the current live version.

    - If already imported, returns sys.modules[module_name] (hot-swap safe).
    - If not yet imported, performs a fresh import.
    - Never raises ImportError — returns None and logs a warning instead.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        logger.warning("safe_import(%s) failed: %s", module_name, exc)
        return None


def reload_extension(module_name: str) -> bool:
    """
    Force-reload *module_name* from disk.

    Useful after a hot-swap build completes.  Returns True on success.
    """
    try:
        importlib.invalidate_caches()
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
        else:
            importlib.import_module(module_name)
        logger.info("Reloaded extension: %s", module_name)
        return True
    except Exception as exc:
        logger.error("Reload failed for %s: %s", module_name, exc)
        return False


def load_with_fallback(cython_module: str, python_fallback: str) -> Any:
    """
    Try to load the compiled Cython extension; fall back to pure Python.

    Parameters
    ----------
    cython_module:
        Dotted module name for the compiled extension (e.g. ``engine.portfolio``).
    python_fallback:
        Dotted module name for the pure-Python fallback (e.g. ``engine.portfolio_py``).

    Returns the loaded module object.
    """
    if extension_available(cython_module):
        mod = safe_import(cython_module)
        if mod is not None:
            return mod
    return safe_import(python_fallback)
