"""
SushiSwap V2 on-chain price reader (Ethereum Mainnet).

SushiSwap V2 is a Uniswap V2 fork — prices are read from the pair reserves
via getAmountsOut() on the SushiSwap Router, which requires no gas.

Mainnet addresses:
  SushiSwap Router V2 : 0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F
  WETH                : 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
  USDC                : 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
"""

from web3 import Web3
from typing import Optional

SUSHI_ROUTER = "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F"
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

ROUTER_ABI = [
    {
        "name": "getAmountsOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn",  "type": "uint256"},
            {"name": "path",      "type": "address[]"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    }
]


class SushiSwap:
    """
    Queries SushiSwap V2 Router for spot prices using getAmountsOut().
    All calls are view-only — no gas cost.
    """

    def __init__(self, rpc_url: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(SUSHI_ROUTER),
            abi=ROUTER_ABI,
        )

    def is_connected(self) -> bool:
        return self.w3.is_connected()

    def get_eth_price_usdc(self) -> Optional[float]:
        """
        Return ETH/USD price from the SushiSwap WETH→USDC pair.
        Simulates selling 1 WETH and reads the USDC output.
        """
        try:
            amount_in = self.w3.to_wei(1, "ether")  # 1 WETH (18 decimals)
            path = [
                Web3.to_checksum_address(WETH_ADDRESS),
                Web3.to_checksum_address(USDC_ADDRESS),
            ]
            amounts = self.router.functions.getAmountsOut(amount_in, path).call()
            # amounts[1] is USDC out (6 decimals)
            return amounts[1] / 1e6
        except Exception as e:
            print(f"[SushiSwap] Price quote failed: {e}")
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
        except Exception as e:
            print(f"[SushiSwap] getAmountsOut failed: {e}")
            return None
