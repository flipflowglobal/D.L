"""
engine/compiler/calldata_builder.py
=====================================
Flash-loan calldata and transaction builder utilities.

Provides two builders:

FlashLoanArbitrageCalldata
  Encodes calls to FlashLoanArbitrage.initiate(amount, direction,
  amountOutMin, deadline).

NexusFlashCalldataBuilder
  Encodes SwapStep[] structs and calls NexusFlashReceiver.initiate(
  asset, amount, steps, minProfit).

These builders are responsible for:
  • Encoding contract calldata
  • Estimating gas where supported by the builder methods
  • Building ready-to-sign transaction dicts

Signing and broadcasting transactions are handled outside this module.

Usage
-----
from engine.compiler.calldata_builder import FlashLoanArbitrageCalldata, SwapStep, DEX

# Simple arb (FlashLoanArbitrage)
builder = FlashLoanArbitrageCalldata(
    contract_address="0xDEPLOYED...",
    abi=REGISTRY["FlashLoanArbitrage"].abi,
    w3=w3,
)
calldata = builder.build(
    amount_wei=int(1e18),
    direction=0,          # 0 = buy_uni_sell_sushi
    amount_out_min=0,
    deadline=int(time.time()) + 120,
)

# Multi-DEX arb (NexusFlashReceiver)
from engine.compiler.calldata_builder import NexusFlashCalldataBuilder, SwapStep, DEX

steps = [
    SwapStep(dex=DEX.UNI_V3, router=UNI_ROUTER, token_in=WETH, token_out=USDC,
             amount_out_min=0, fee=3000, deadline=int(time.time())+120),
    SwapStep(dex=DEX.SUSHI_V2, router=SUSHI_ROUTER, token_in=USDC, token_out=WETH,
             amount_out_min=0, fee=0, deadline=int(time.time())+120),
]
builder = NexusFlashCalldataBuilder(
    contract_address="0xDEPLOYED...",
    abi=REGISTRY["NexusFlashReceiver"].abi,
    w3=w3,
)
tx = builder.build(
    asset=WETH,
    amount_wei=int(10e18),
    steps=steps,
    min_profit_wei=int(0.001e18),
)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from web3 import Web3


# ── DEX type constants (mirrors NexusFlashReceiver.sol) ───────────────────────

class DEX(IntEnum):
    UNI_V3   = 0
    SUSHI_V2 = 1
    CURVE    = 2
    BALANCER = 3
    CAMELOT  = 4


# ── SwapStep dataclass ────────────────────────────────────────────────────────

@dataclass
class SwapStep:
    """
    One swap hop in a multi-DEX flash-loan route.

    Mirrors the Solidity struct NexusFlashReceiver.SwapStep.
    """
    dex:            int             # DEX constant (DEX.*)
    router:         str             # router / vault / pool address (checksum)
    token_in:       str             # ERC-20 in
    token_out:      str             # ERC-20 out
    amount_out_min: int  = 0        # slippage guard (0 = accept any)
    fee:            int  = 3000     # Uniswap V3 fee tier (ignored for V2/Curve/Balancer)
    balancer_pool_id: bytes = field(default_factory=lambda: b"\x00" * 32)
    curve_i:        int  = 0        # Curve token-in index
    curve_j:        int  = 1        # Curve token-out index
    deadline:       int  = 0        # Unix ts; 0 = auto (now + 120s)

    def resolved_deadline(self) -> int:
        return self.deadline if self.deadline > 0 else int(time.time()) + 120

    def to_tuple(self) -> tuple:
        """
        Return the step as a tuple matching the Solidity struct field order:
        (dex, router, tokenIn, tokenOut, amountOutMin, fee,
         balancerPoolId, curveI, curveJ, deadline)
        """
        pool_id = self.balancer_pool_id
        if isinstance(pool_id, str):
            pool_id = bytes.fromhex(pool_id.removeprefix("0x").ljust(64, "0"))
        pool_id_32 = pool_id[:32].ljust(32, b"\x00")

        return (
            self.dex,
            Web3.to_checksum_address(self.router),
            Web3.to_checksum_address(self.token_in),
            Web3.to_checksum_address(self.token_out),
            self.amount_out_min,
            self.fee,
            pool_id_32,
            self.curve_i,
            self.curve_j,
            self.resolved_deadline(),
        )


# ── Encode SwapStep[] for NexusFlashReceiver.initiate() ──────────────────────

def encode_steps(steps: list[SwapStep]) -> bytes:
    """
    ABI-encode a list of SwapStep structs into bytes suitable for the
    NexusFlashReceiver.initiate(asset, amount, steps, minProfit) call.

    The Solidity contract expects:  abi.decode(steps, (SwapStep[]))
    which means the bytes are produced by:  abi.encode(swapSteps)
    """
    from eth_abi import encode  # type: ignore[import-untyped]

    # Solidity tuple type for a single SwapStep
    STEP_TYPE = "(uint8,address,address,address,uint256,uint24,bytes32,int128,int128,uint256)"

    tuples = [s.to_tuple() for s in steps]
    return encode([f"{STEP_TYPE}[]"], [tuples])


# ── FlashLoanArbitrage calldata builder ───────────────────────────────────────

class FlashLoanArbitrageCalldata:
    """
    Builds transactions for FlashLoanArbitrage.initiate().

    Direction constants
    -------------------
    0  BUY_UNI_SELL_SUSHI  — buy USDC on UniV3, sell back on SushiV2
    1  BUY_SUSHI_SELL_UNI  — buy USDC on SushiV2, sell back on UniV3
    """

    BUY_UNI_SELL_SUSHI = 0
    BUY_SUSHI_SELL_UNI = 1

    def __init__(
        self,
        contract_address: str,
        abi: list[dict[str, Any]],
        w3: Web3,
    ):
        self.w3       = w3
        self.contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=abi,
        )

    def build(
        self,
        sender: str,
        amount_wei: int,
        direction: int,
        amount_out_min: int,
        deadline: int | None = None,
        gas_buffer: float = 1.20,
    ) -> dict[str, Any]:
        """
        Return a transaction dict ready for sign_transaction().

        Parameters
        ----------
        sender         : caller's address (must be contract owner)
        amount_wei     : WETH to borrow (in wei)
        direction      : 0 or 1 (see class constants)
        amount_out_min : minimum USDC from the first swap (slippage guard)
        deadline       : Unix timestamp; default = now + 120 s
        gas_buffer     : multiply estimatedGas by this factor (default 1.20)
        """
        if deadline is None:
            deadline = int(time.time()) + 120

        fn = self.contract.functions.initiate(
            amount_wei,
            direction,
            amount_out_min,
            deadline,
        )

        gas_estimate = fn.estimate_gas({"from": Web3.to_checksum_address(sender)})
        gas_limit    = int(gas_estimate * gas_buffer)

        tx = fn.build_transaction({
            "from":  Web3.to_checksum_address(sender),
            "gas":   gas_limit,
            "nonce": self.w3.eth.get_transaction_count(
                Web3.to_checksum_address(sender)
            ),
        })
        return tx

    def encode_params(
        self,
        amount_wei: int,
        direction: int,
        amount_out_min: int,
        deadline: int | None = None,
    ) -> bytes:
        """Return raw calldata bytes (useful for inspection / testing)."""
        if deadline is None:
            deadline = int(time.time()) + 120
        return self.contract.encode_abi(
            "initiate",
            args=[amount_wei, direction, amount_out_min, deadline],
        )


# ── NexusFlashReceiver calldata builder ───────────────────────────────────────

class NexusFlashCalldataBuilder:
    """
    Builds transactions for NexusFlashReceiver.initiate().

    Accepts a list of SwapStep objects, ABI-encodes them, and constructs
    the transaction.  Optionally estimates gas before building.
    """

    def __init__(
        self,
        contract_address: str,
        abi: list[dict[str, Any]],
        w3: Web3,
    ):
        self.w3       = w3
        self.contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=abi,
        )

    def build(
        self,
        sender: str,
        asset: str,
        amount_wei: int,
        steps: list[SwapStep],
        min_profit_wei: int = 0,
        gas_buffer: float = 1.20,
    ) -> dict[str, Any]:
        """
        Return a transaction dict ready for sign_transaction().

        Parameters
        ----------
        sender         : caller's address (must be contract owner)
        asset          : ERC-20 token address to borrow
        amount_wei     : amount to borrow
        steps          : list of SwapStep objects
        min_profit_wei : minimum net profit (reverts if not met)
        gas_buffer     : multiply estimatedGas by this factor
        """
        steps_encoded = encode_steps(steps)

        fn = self.contract.functions.initiate(
            Web3.to_checksum_address(asset),
            amount_wei,
            steps_encoded,
            min_profit_wei,
        )

        gas_estimate = fn.estimate_gas({"from": Web3.to_checksum_address(sender)})
        gas_limit    = int(gas_estimate * gas_buffer)

        tx = fn.build_transaction({
            "from":  Web3.to_checksum_address(sender),
            "gas":   gas_limit,
            "nonce": self.w3.eth.get_transaction_count(
                Web3.to_checksum_address(sender)
            ),
        })
        return tx

    def encode_params(
        self,
        asset: str,
        amount_wei: int,
        steps: list[SwapStep],
        min_profit_wei: int = 0,
    ) -> bytes:
        """Return raw calldata bytes (useful for inspection / testing)."""
        steps_encoded = encode_steps(steps)
        return self.contract.encode_abi(
            "initiate",
            args=[
                Web3.to_checksum_address(asset),
                amount_wei,
                steps_encoded,
                min_profit_wei,
            ],
        )

    def estimate_gas(
        self,
        sender: str,
        asset: str,
        amount_wei: int,
        steps: list[SwapStep],
        min_profit_wei: int = 0,
    ) -> int:
        """Return the on-chain gas estimate for initiate()."""
        steps_encoded = encode_steps(steps)
        return self.contract.functions.initiate(
            Web3.to_checksum_address(asset),
            amount_wei,
            steps_encoded,
            min_profit_wei,
        ).estimate_gas({"from": Web3.to_checksum_address(sender)})
