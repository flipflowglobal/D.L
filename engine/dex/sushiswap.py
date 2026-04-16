"""
engine/dex/sushiswap.py — SushiSwap V2 on-chain price reader (Ethereum Mainnet).

Performance improvements over original:
  - get_eth_price_usdc_async(): non-blocking via run_in_executor so it can be
    gathered concurrently with the Uniswap V3 queries.
  - Persistent HTTPProvider with connection pooling.

Mainnet addresses:
  SushiSwap Router V2 : 0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F
  WETH                : 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
  USDC                : 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
"""

from __future__ import annotations

import asyncio
from typing import Optional

from web3 import Web3

SUSHI_ROUTER = "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F"
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

_ONE_ETH_WEI = 10 ** 18

ROUTER_ABI = [
    {
        "name":            "getAmountsOut",
        "type":            "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path",     "type": "address[]"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    }
]


class SushiSwap:
    """
    Queries SushiSwap V2 Router for spot prices via getAmountsOut().
    All calls are view-only — no gas cost.
    """

    def __init__(self, rpc_url: str):
        self.w3 = Web3(Web3.HTTPProvider(
            rpc_url,
            request_kwargs={"timeout": 10},
        ))
        self._weth = Web3.to_checksum_address(WETH_ADDRESS)
        self._usdc = Web3.to_checksum_address(USDC_ADDRESS)
        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(SUSHI_ROUTER),
            abi=ROUTER_ABI,
        )

    def is_connected(self) -> bool:
        return self.w3.is_connected()

    # ── synchronous ───────────────────────────────────────────────────────────

    def get_eth_price_usdc(self) -> Optional[float]:
        """
        Return ETH/USD price from the SushiSwap WETH→USDC pair.
        Simulates selling 1 WETH and reads the USDC output.
        """
        try:
            amounts = self.router.functions.getAmountsOut(
                _ONE_ETH_WEI,
                [self._weth, self._usdc],
            ).call()
            return amounts[1] / 1e6    # USDC has 6 decimals
        except Exception as exc:
            print(f"[SushiSwap] Price quote failed: {exc}")
            return None

    def get_amounts_out(
        self, amount_in_wei: int, path: list
    ) -> Optional[list]:
        """Generic getAmountsOut for any token path."""
        try:
            checksummed = [Web3.to_checksum_address(t) for t in path]
            return self.router.functions.getAmountsOut(
                amount_in_wei, checksummed
            ).call()
        except Exception as exc:
            print(f"[SushiSwap] getAmountsOut failed: {exc}")
            return None

    # ── async versions ────────────────────────────────────────────────────────

    async def get_eth_price_usdc_async(self) -> Optional[float]:
        """
        Non-blocking wrapper for get_eth_price_usdc().

        Designed to be gathered concurrently with Uniswap V3 fee-tier queries
        so that both DEX prices are fetched in parallel:

            uni_price, sushi_price = await asyncio.gather(
                uni.get_best_eth_price_async(),
                sushi.get_eth_price_usdc_async(),
            )
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_eth_price_usdc)

    async def get_amounts_out_async(
        self, amount_in_wei: int, path: list
    ) -> Optional[list]:
        """Async wrapper for get_amounts_out."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.get_amounts_out, amount_in_wei, path
        )
