"""
engine/compiler/__init__.py
============================
NexusSolidityEngine package — Solidity compilation for flash-loan contracts.

Quick-start
-----------
    from engine.compiler import NexusSolidityEngine, contract_registry

    engine = NexusSolidityEngine()
    spec   = contract_registry.get("NexusFlashReceiver")
    result = engine.compile(spec, chain_id=11155111)

    address = engine.deploy(spec, result,
                            rpc_url=..., private_key=...)
"""

from engine.compiler.solidity_engine import NexusSolidityEngine, CompileResult
from engine.compiler import contract_registry
from engine.compiler.calldata_builder import (
    DEX,
    SwapStep,
    encode_steps,
    FlashLoanArbitrageCalldata,
    NexusFlashCalldataBuilder,
)

__all__ = [
    "NexusSolidityEngine",
    "CompileResult",
    "contract_registry",
    "DEX",
    "SwapStep",
    "encode_steps",
    "FlashLoanArbitrageCalldata",
    "NexusFlashCalldataBuilder",
]
