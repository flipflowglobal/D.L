# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/flash_loan_executor.py — Python executor for NexusFlashReceiver.sol.

Converts an ArbitrageOpportunity (from Bellman-Ford) into on-chain calldata
and submits the `executeArbitrage()` transaction via the TransactionManager.

Architecture:
  1. ArbitrageOpportunity describes a token cycle + pools
  2. FlashLoanExecutor encodes each pool as a SwapStep struct
  3. Calls NexusFlashReceiver.executeArbitrage(asset, amount, steps[])
  4. TransactionManager handles EIP-1559 fees, nonce, and confirmation
  5. Profit flows back to the owner wallet on-chain

NexusFlashReceiver SwapStep struct:
  uint8   dexType      (0=UniV3, 1=Curve, 2=Balancer, 3=Camelot)
  address tokenIn
  address tokenOut
  uint256 amountIn     (0 = use full balance)
  uint256 minAmountOut (slippage guard)
  bytes   extraData    (DEX-specific ABI-encoded params)
"""

from __future__ import annotations

import logging
import os
from web3 import Web3
from eth_abi import encode as abi_encode

log = logging.getLogger(__name__)

# ── NexusFlashReceiver ABI (executeArbitrage entry point only) ────────────────
_NEXUS_ABI = [
    {
        "name": "executeArbitrage",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "asset",  "type": "address"},
            {"name": "amount", "type": "uint256"},
            {
                "name": "steps",
                "type": "tuple[]",
                "components": [
                    {"name": "dexType",      "type": "uint8"},
                    {"name": "tokenIn",      "type": "address"},
                    {"name": "tokenOut",     "type": "address"},
                    {"name": "amountIn",     "type": "uint256"},
                    {"name": "minAmountOut", "type": "uint256"},
                    {"name": "extraData",    "type": "bytes"},
                ],
            },
        ],
        "outputs": [],
    },
    {
        "name": "totalProfit",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "paused",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "bool"}],
    },
]

# DEX type constants — must match NexusFlashReceiver.sol
DEX_UNISWAP_V3 = 0
DEX_CURVE      = 1
DEX_BALANCER   = 2
DEX_CAMELOT_V3 = 3

# Default DEX → type mapping
_DEX_TYPE_MAP = {
    "uniswap_v3":  DEX_UNISWAP_V3,
    "uniswap":     DEX_UNISWAP_V3,
    "sushiswap":   DEX_UNISWAP_V3,  # SushiSwap uses same V3 router interface
    "curve":       DEX_CURVE,
    "balancer":    DEX_BALANCER,
    "balancer_v2": DEX_BALANCER,
    "camelot_v3":  DEX_CAMELOT_V3,
    "camelot":     DEX_CAMELOT_V3,
}

# Default Uniswap V3 fee tiers
_DEX_DEFAULT_FEE = {
    "uniswap_v3": 500,     # 0.05% — cheapest WETH/USDC pool
    "sushiswap":  3000,    # 0.3%
    "camelot_v3": 2500,    # 0.25% — Camelot default
}

# Known Balancer pool IDs for common pairs (mainnet)
_BALANCER_POOL_IDS: dict[tuple[str, str], bytes] = {
    # WETH/USDC 0.3% Balancer pool
    ("WETH", "USDC"): bytes.fromhex(
        "96646936b91d6b9d7d0c47c496afbf3d6ec7b6f8000200000000000000000019"
    ),
}


def _encode_uniswap_extra(fee: int, router_override: str = "") -> bytes:
    """Encode (uint24 fee, address routerOverride) for UniV3/Camelot steps."""
    router_addr = Web3.to_checksum_address(router_override) if router_override else \
        "0x0000000000000000000000000000000000000000"
    return abi_encode(["uint24", "address"], [fee, router_addr])


def _encode_balancer_extra(pool_id: bytes) -> bytes:
    """Encode bytes32 poolId for Balancer steps."""
    return abi_encode(["bytes32"], [pool_id])


def _resolve_token_address(symbol: str) -> str:
    """
    Map common token symbols to mainnet addresses.
    Falls back to the symbol itself if it looks like an address.
    """
    _ADDRESSES = {
        "WETH":  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "ETH":   "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "USDC":  "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT":  "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI":   "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "WBTC":  "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        "FRAX":  "0x853d955aCEf822Db058eb8505911ED77F175b99e",
        "LINK":  "0x514910771AF9Ca656af840dff83E8264EcF986CA",
        "UNI":   "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        "AAVE":  "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
    }
    if symbol in _ADDRESSES:
        return _ADDRESSES[symbol]
    if symbol.startswith("0x") and len(symbol) == 42:
        return Web3.to_checksum_address(symbol)
    raise ValueError(f"Unknown token symbol: {symbol!r}. Provide a 0x address instead.")


class SwapStep:
    """Python representation of the on-chain SwapStep struct."""

    __slots__ = ("dex_type", "token_in", "token_out", "amount_in", "min_amount_out", "extra_data")

    def __init__(
        self,
        dex_type: int,
        token_in: str,
        token_out: str,
        amount_in: int,
        min_amount_out: int,
        extra_data: bytes,
    ) -> None:
        self.dex_type      = dex_type
        self.token_in      = token_in
        self.token_out     = token_out
        self.amount_in     = amount_in
        self.min_amount_out = min_amount_out
        self.extra_data    = extra_data

    def to_tuple(self) -> tuple:
        return (
            self.dex_type,
            self.token_in,
            self.token_out,
            self.amount_in,
            self.min_amount_out,
            self.extra_data,
        )


class FlashLoanExecutor:
    """
    Submits flash loan arbitrage transactions to NexusFlashReceiver.sol.

    Usage:
        executor = FlashLoanExecutor(w3, contract_address, tx_manager)
        receipt  = executor.execute(opportunity, borrow_amount_eth)
    """

    # Minimum gas to reserve for the flash loan overhead (Aave callback + repay)
    BASE_GAS     = 250_000
    GAS_PER_STEP = 150_000   # Additional gas per swap step

    def __init__(
        self,
        w3: Web3,
        contract_address: str,
        tx_manager,                       # engine.mainnet.TransactionManager
        slippage: float = 0.005,          # 0.5% default slippage guard
    ) -> None:
        self.w3               = w3
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.tx_manager       = tx_manager
        self.slippage         = slippage
        self.contract         = w3.eth.contract(
            address=self.contract_address,
            abi=_NEXUS_ABI,
        )

    # ── Public execute ────────────────────────────────────────────────────────

    def execute(
        self,
        opportunity,
        borrow_amount_eth: float,
        eth_price_usd: float = 0.0,
        dry_run: bool = False,
    ):
        """
        Convert an ArbitrageOpportunity (legacy) into an on-chain flash loan transaction.

        Args:
            opportunity:       ArbitrageOpportunity from BellmanFord.detect() (legacy)
            borrow_amount_eth: Amount of WETH to borrow (in ETH units)
            eth_price_usd:     Current ETH price (informational only)
            dry_run:           If True, build and log but do NOT broadcast

        Returns:
            TxReceipt (or None if dry_run=True)
        """
        # Validate opportunity structure before any on-chain work
        if not opportunity.cycle or len(opportunity.cycle) < 3:
            raise ValueError(
                f"ArbitrageOpportunity must have cycle of length ≥3, "
                f"got: {opportunity.cycle!r}"
            )
        if not opportunity.pools or len(opportunity.pools) != len(opportunity.cycle) - 1:
            raise ValueError(
                f"ArbitrageOpportunity pools count ({len(opportunity.pools)}) "
                f"must equal cycle length - 1 ({len(opportunity.cycle) - 1})"
            )

        return self._execute_cycle(
            cycle=opportunity.cycle,
            cycle_edges=None,
            pools=opportunity.pools,
            borrow_amount_eth=borrow_amount_eth,
            dry_run=dry_run,
        )

    def execute_from_result(
        self,
        result,
        borrow_amount_eth: float,
        dry_run: bool = False,
    ):
        """
        Execute a flash loan from a BellmanFordArb ArbitrageResult.

        Args:
            result:            ArbitrageResult from BellmanFordArb.find_best_arbitrage()
            borrow_amount_eth: Amount of WETH to borrow (in ETH units)
            dry_run:           If True, build and log but do NOT broadcast

        Returns:
            TxReceipt (or None if dry_run=True or no cycle detected)
        """
        if not result.has_cycle or len(result.cycle) < 3:
            log.info("[FlashLoanExecutor] No profitable cycle in ArbitrageResult — skipping")
            return None

        return self._execute_cycle(
            cycle=result.cycle,
            cycle_edges=result.cycle_edges,
            pools=None,
            borrow_amount_eth=borrow_amount_eth,
            dry_run=dry_run,
        )

    def _execute_cycle(
        self,
        cycle: list,
        cycle_edges,
        pools,
        borrow_amount_eth: float,
        dry_run: bool,
    ):
        """Shared execution path for both execute() and execute_from_result()."""
        # Check contract is not paused
        if self._is_paused():
            log.warning("[FlashLoanExecutor] Contract is paused — skipping")
            return None

        borrow_wei   = self.w3.to_wei(borrow_amount_eth, "ether")
        borrow_token = _resolve_token_address(cycle[0])
        steps        = self._build_steps_from_cycle(cycle, cycle_edges, pools, borrow_wei)
        gas_limit    = self.BASE_GAS + self.GAS_PER_STEP * len(steps)

        log.info(
            f"[FlashLoanExecutor] cycle={cycle} "
            f"borrow={borrow_amount_eth} ETH  steps={len(steps)}  gas={gas_limit:,}"
        )

        if dry_run:
            log.info("[FlashLoanExecutor] dry_run=True — not broadcasting")
            return None

        # ABI-encode the calldata and dispatch via TransactionManager
        calldata = self.contract.encodeABI(
            fn_name="executeArbitrage",
            args=[borrow_token, borrow_wei, [s.to_tuple() for s in steps]],
        )
        tx = self.tx_manager.build_tx(
            to=self.contract_address,
            value_wei=0,
            data=Web3.to_bytes(hexstr=calldata),
            gas_limit=gas_limit,
        )
        return self.tx_manager.send_and_confirm(tx)

    # ── Step builder ──────────────────────────────────────────────────────────

    def _build_steps_from_cycle(
        self,
        cycle: list,
        cycle_edges,        # list of (from, to, rate) tuples from BellmanFordArb, or None
        pools,              # legacy PoolPrice list, or None
        borrow_wei: int,
    ) -> list[SwapStep]:
        """
        Convert a token cycle into a list of SwapStep objects.

        Supports two input formats:
          1. cycle_edges from BellmanFordArb (from, to, rate) — new API
          2. pools list of PoolPrice objects — legacy API
        """
        steps: list[SwapStep] = []
        hops  = list(zip(cycle[:-1], cycle[1:]))

        for i, (token_in_sym, token_out_sym) in enumerate(hops):
            token_in  = _resolve_token_address(token_in_sym)
            token_out = _resolve_token_address(token_out_sym)

            if pools is not None:
                # Legacy path: use PoolPrice info
                pool     = pools[i]
                dex_type = _DEX_TYPE_MAP.get(pool.dex.lower(), DEX_UNISWAP_V3)
                rate     = getattr(pool, "price_after_fee", 1.0)
                extra    = self._build_extra_data_from_pool(dex_type, pool, token_in_sym, token_out_sym)
            elif cycle_edges is not None and i < len(cycle_edges):
                # New path: use cycle_edges (from, to, rate) from BellmanFordArb
                _, _, rate = cycle_edges[i]
                dex_type = DEX_UNISWAP_V3  # default DEX; no DEX metadata in new format
                extra    = _encode_uniswap_extra(_DEX_DEFAULT_FEE.get("uniswap_v3", 500))
            else:
                rate     = 1.0
                dex_type = DEX_UNISWAP_V3
                extra    = _encode_uniswap_extra(500)

            min_out = int(borrow_wei * rate * (1 - self.slippage)) if i == 0 else 0

            steps.append(SwapStep(
                dex_type=dex_type,
                token_in=token_in,
                token_out=token_out,
                amount_in=0,         # 0 = use full contract balance (chained swaps)
                min_amount_out=min_out,
                extra_data=extra,
            ))

        return steps

    def _build_steps(self, opportunity, borrow_wei: int) -> list[SwapStep]:
        """Legacy step builder for ArbitrageOpportunity objects."""
        return self._build_steps_from_cycle(
            cycle=opportunity.cycle,
            cycle_edges=None,
            pools=opportunity.pools,
            borrow_wei=borrow_wei,
        )

    def _build_extra_data_from_pool(
        self,
        dex_type: int,
        pool,
        token_in_sym: str,
        token_out_sym: str,
    ) -> bytes:
        """Build DEX-specific ABI-encoded extra data for a swap step."""
        return self._build_extra_data(dex_type, pool, token_in_sym, token_out_sym)

    def _build_extra_data(
        self,
        dex_type: int,
        pool,
        token_in_sym: str,
        token_out_sym: str,
    ) -> bytes:
        """Build DEX-specific ABI-encoded extra data for a swap step."""
        if dex_type in (DEX_UNISWAP_V3, DEX_CAMELOT_V3):
            fee = _DEX_DEFAULT_FEE.get(pool.dex.lower(), 500)
            return _encode_uniswap_extra(fee)

        if dex_type == DEX_BALANCER:
            pool_id_key = (token_in_sym, token_out_sym)
            pool_id     = _BALANCER_POOL_IDS.get(
                pool_id_key,
                # Default: zero pool ID (caller must set correct ID)
                b"\x00" * 32,
            )
            return _encode_balancer_extra(pool_id)

        if dex_type == DEX_CURVE:
            # Curve Router extra data is complex (route + swap_params + pools)
            # For now return empty bytes — the contract handles Curve router calls
            # in a separate integration path
            return b""

        return b""

    # ── Validation ───────────────────────────────────────────────────────────

    def _validate_opportunity(self, opportunity) -> None:
        """
        Validate an ArbitrageOpportunity or ArbitrageResult before execution.

        Raises ValueError for:
          - cycle shorter than 3 tokens
          - pools count != cycle length - 1 (legacy ArbitrageOpportunity only)
        """
        cycle = getattr(opportunity, "cycle", None) or []
        if len(cycle) < 3:
            raise ValueError(
                f"Opportunity cycle must have ≥3 tokens, got: {cycle!r}"
            )
        # Legacy ArbitrageOpportunity has a pools attribute
        pools = getattr(opportunity, "pools", None)
        if pools is not None and len(pools) != len(cycle) - 1:
            raise ValueError(
                f"Opportunity pools count ({len(pools)}) must equal "
                f"cycle length - 1 ({len(cycle) - 1})"
            )

    def validate_opportunity(self, opportunity) -> tuple:
        """
        Non-raising validation — suitable for pre-flight checks.

        Returns
        -------
        (True, "ok")              if all checks pass
        (False, <reason string>)  if any check fails
        """
        try:
            self._validate_opportunity(opportunity)
            return True, "ok"
        except ValueError as exc:
            return False, str(exc)

    # ── Status helpers ────────────────────────────────────────────────────────

    def _is_paused(self) -> bool:
        try:
            return bool(self.contract.functions.paused().call())
        except Exception:
            return False

    def total_profit_eth(self) -> float:
        """Read accumulated on-chain profit from the contract."""
        try:
            wei = self.contract.functions.totalProfit().call()
            return wei / 1e18
        except Exception:
            return 0.0

    @classmethod
    def from_env(cls, w3: Web3, tx_manager, slippage: float = 0.005):
        """Construct from environment variables (FLASH_RECEIVER_ADDRESS)."""
        addr = os.getenv("FLASH_RECEIVER_ADDRESS")
        if not addr:
            raise ValueError(
                "FLASH_RECEIVER_ADDRESS not set. "
                "Deploy NexusFlashReceiver.sol and set this variable."
            )
        return cls(w3, addr, tx_manager, slippage)
