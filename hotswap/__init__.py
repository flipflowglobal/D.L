"""
hotswap — Zero-downtime Cython and Rust extension hot-reloading.

Usage:
    from hotswap import HotSwapController
    ctl = HotSwapController()
    ctl.start()          # background file-watcher + rebuild loop
    ...
    ctl.stop()
"""

from .controller import HotSwapController

__all__ = ["HotSwapController"]
