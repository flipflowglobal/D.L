"""nexus_arb.math — mathematical primitives re-export surface.

Modules are imported lazily to avoid ImportError when submodules
are not yet implemented.  Access individual classes after verifying
the module is available.
"""

__all__ = [
    "GasCostModel",
    "SlippageModel",
    "BondingCurve",
    "BondingCurveType",
    "MarkovChain",
    "MM1Queue",
    "MD1Queue",
]


def __getattr__(name: str):
    """Lazy loader — only raise ImportError when an attribute is actually accessed."""
    _module_map = {
        "GasCostModel":     ("nexus_arb.math.cost_model",    "GasCostModel"),
        "SlippageModel":    ("nexus_arb.math.cost_model",    "SlippageModel"),
        "BondingCurve":     ("nexus_arb.math.bonding_curve", "BondingCurve"),
        "BondingCurveType": ("nexus_arb.math.bonding_curve", "BondingCurveType"),
        "MarkovChain":      ("nexus_arb.math.markov_chain",  "MarkovChain"),
        "MM1Queue":         ("nexus_arb.math.queueing",      "MM1Queue"),
        "MD1Queue":         ("nexus_arb.math.queueing",      "MD1Queue"),
    }
    if name in _module_map:
        mod_path, attr = _module_map[name]
        import importlib
        mod = importlib.import_module(mod_path)
        return getattr(mod, attr)
    raise AttributeError(f"module 'nexus_arb.math' has no attribute {name!r}")
