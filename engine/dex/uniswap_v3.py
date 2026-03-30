"""
Uniswap V3 on-chain price quoter (Ethereum Mainnet).

Uses the read-only Quoter contract so no gas is spent.
Prices are fetched by simulating a swap and reading the amountOut.

Mainnet addresses used:
  Quoter V1 : 0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6
  WETH      : 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
  USDC      : 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
"""

from web3 import Web3
from typing import Optional

# ── Mainnet constants ─────────────────────────────────────────────────────────
QUOTER_ADDRESS = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"
WETH_ADDRESS   = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDRESS   = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# Pool fee tiers (basis points × 100): 0.05 %, 0.3 %, 1 %
FEE_LOW    = 500
FEE_MEDIUM = 3000
FEE_HIGH   = 10000

QUOTER_ABI = [
    {
        "name": "quoteExactInputSingle",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenIn",            "type": "address"},
            {"name": "tokenOut",           "type": "address"},
            {"name": "fee",                "type": "uint24"},
            {"name": "amountIn",           "type": "uint256"},
            {"name": "sqrtPriceLimitX96",  "type": "uint256"},
        ],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
    }
]


class UniswapV3:
    """
    Queries on-chain swap output amounts via Uniswap V3 Quoter.
    No wallet / signing required — all calls are eth_call (read-only).
    """

    def __init__(self, rpc_url: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.quoter = self.w3.eth.contract(
            address=Web3.to_checksum_address(QUOTER_ADDRESS),
            abi=QUOTER_ABI,
        )

    def is_connected(self) -> bool:
        return self.w3.is_connected()

    def get_eth_price_usdc(self, fee: int = FEE_LOW) -> Optional[float]:
        """
        Return the current WETH → USDC spot price on Uniswap V3.
        Swaps 1 ETH worth of WETH and reads how many USDC come out.
        Returns USD price as a float, or None on failure.
        """
        try:
            amount_in = self.w3.to_wei(1, "ether")  # 1 WETH
            amount_out_raw = self.quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(WETH_ADDRESS),
                Web3.to_checksum_address(USDC_ADDRESS),
                fee,
                amount_in,
                0,  # no sqrt price limit
            ).call()
            # USDC has 6 decimals
            return amount_out_raw / 1e6
        except Exception as e:
            print(f"[UniswapV3] Price quote failed (fee={fee}): {e}")
            return None

    def get_best_eth_price(self) -> Optional[float]:
        """Try all fee tiers and return the best (highest) USDC price."""
        prices = []
        for fee in (FEE_LOW, FEE_MEDIUM, FEE_HIGH):
            p = self.get_eth_price_usdc(fee)
            if p is not None and p > 0:
                prices.append(p)
        return max(prices) if prices else None

    def quote_token_out(
        self,
        token_in: str,
        token_out: str,
        amount_in_wei: int,
        fee: int = FEE_MEDIUM,
    ) -> Optional[int]:
        """Generic quote: returns raw amountOut for any token pair."""
        try:
            return self.quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out),
                fee,
                amount_in_wei,
                0,
            ).call()
        except Exception as e:
            print(f"[UniswapV3] quote_token_out failed: {e}")
            return None
