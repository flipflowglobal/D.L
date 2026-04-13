"""
Uniswap V3 Swap Executor (Ethereum Mainnet).

Executes exact-input swaps via the Uniswap V3 SwapRouter.
Supports:
  • ETH  → ERC-20  (wraps ETH as msg.value)
  • ERC-20 → ERC-20 (requires prior token approval)

Mainnet addresses:
  SwapRouter V1 : 0xE592427A0AEce92De3Edee1F18E0157C05861564
  WETH          : 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
  USDC          : 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
"""

import time
from typing import Optional
from web3 import Web3

from vault.wallet_config import WalletConfig

SWAP_ROUTER    = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
WETH_ADDRESS   = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDRESS   = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

ERC20_APPROVE_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner",   "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

ROUTER_ABI = [
    {
        "name": "exactInputSingle",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {"name": "tokenIn",           "type": "address"},
                    {"name": "tokenOut",          "type": "address"},
                    {"name": "fee",               "type": "uint24"},
                    {"name": "recipient",         "type": "address"},
                    {"name": "deadline",          "type": "uint256"},
                    {"name": "amountIn",          "type": "uint256"},
                    {"name": "amountOutMinimum",  "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint256"},
                ],
            }
        ],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
    }
]


class SwapExecutor:
    """
    Signs and broadcasts swaps on Uniswap V3 SwapRouter.

    Usage:
        executor = SwapExecutor(wallet, rpc_url)
        tx_hash = executor.swap_eth_to_usdc(amount_eth=0.1, slippage=0.005)
    """

    DEFAULT_FEE      = 500      # 0.05 % pool (cheapest ETH/USDC pool)
    DEADLINE_SECONDS = 300      # 5-minute deadline

    def __init__(self, wallet: WalletConfig, rpc_url: str):
        self.wallet = wallet
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError(f"Web3 connection failed: {rpc_url}")
        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(SWAP_ROUTER),
            abi=ROUTER_ABI,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _deadline(self) -> int:
        return int(time.time()) + self.DEADLINE_SECONDS

    def _gas_price(self) -> int:
        return self.w3.eth.gas_price

    def _min_out(self, amount: int, slippage: float) -> int:
        """Apply slippage tolerance: amountOut × (1 − slippage)."""
        return int(amount * (1 - slippage))

    def _ensure_approval(self, token_address: str, amount_wei: int) -> None:
        """Approve SwapRouter to spend `amount_wei` of `token_address` if needed."""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_APPROVE_ABI,
        )
        current = token.functions.allowance(
            self.wallet.account.address,
            Web3.to_checksum_address(SWAP_ROUTER),
        ).call()
        if current >= amount_wei:
            return
        # Send approval tx
        nonce = self.w3.eth.get_transaction_count(self.wallet.account.address)
        tx = token.functions.approve(
            Web3.to_checksum_address(SWAP_ROUTER),
            2**256 - 1,  # max approval
        ).build_transaction({
            "from":     self.wallet.account.address,
            "nonce":    nonce,
            "gasPrice": self._gas_price(),
            "gas":      60_000,
        })
        signed = self.wallet.account.sign_transaction(tx)
        self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[SwapExecutor] Approved {token_address[:8]}… for SwapRouter")

    # ── public swap methods ───────────────────────────────────────────────────

    def swap_eth_to_usdc(
        self,
        amount_eth: float,
        slippage: float = 0.005,
        expected_usdc: Optional[float] = None,
    ) -> str:
        """
        Sell `amount_eth` ETH for USDC.
        `slippage` = max acceptable price slippage (default 0.5 %).
        `expected_usdc` = quote amount (used for min-out calc). Falls back
                          to 0 min-out if not provided (use with caution on mainnet).
        Returns hex tx hash.
        """
        amount_in = self.w3.to_wei(amount_eth, "ether")

        # Calculate minimum USDC out with slippage applied
        if expected_usdc is not None:
            min_out = int(expected_usdc * 1e6 * (1 - slippage))
        else:
            min_out = 0  # accepts any amount — risky on mainnet

        params = (
            Web3.to_checksum_address(WETH_ADDRESS),   # tokenIn (ETH sent as WETH)
            Web3.to_checksum_address(USDC_ADDRESS),   # tokenOut
            self.DEFAULT_FEE,
            self.wallet.account.address,               # recipient
            self._deadline(),
            amount_in,
            min_out,
            0,                                         # no price limit
        )

        nonce = self.w3.eth.get_transaction_count(self.wallet.account.address)
        tx = self.router.functions.exactInputSingle(params).build_transaction({
            "from":     self.wallet.account.address,
            "value":    amount_in,   # ETH sent with the tx
            "nonce":    nonce,
            "gasPrice": self._gas_price(),
            "gas":      200_000,
        })

        signed   = self.wallet.account.sign_transaction(tx)
        tx_hash  = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[SwapExecutor] ETH→USDC  {amount_eth} ETH  tx={tx_hash.hex()[:18]}…")
        return tx_hash.hex()

    def swap_usdc_to_eth(
        self,
        amount_usdc: float,
        slippage: float = 0.005,
        expected_eth: Optional[float] = None,
    ) -> str:
        """
        Sell `amount_usdc` USDC for ETH (WETH).
        Returns hex tx hash.
        """
        amount_in = int(amount_usdc * 1e6)  # USDC has 6 decimals
        min_out   = (
            self.w3.to_wei(expected_eth * (1 - slippage), "ether")
            if expected_eth else 0
        )

        self._ensure_approval(USDC_ADDRESS, amount_in)

        params = (
            Web3.to_checksum_address(USDC_ADDRESS),   # tokenIn
            Web3.to_checksum_address(WETH_ADDRESS),   # tokenOut
            self.DEFAULT_FEE,
            self.wallet.account.address,
            self._deadline(),
            amount_in,
            min_out,
            0,
        )

        nonce = self.w3.eth.get_transaction_count(self.wallet.account.address)
        tx = self.router.functions.exactInputSingle(params).build_transaction({
            "from":     self.wallet.account.address,
            "value":    0,
            "nonce":    nonce,
            "gasPrice": self._gas_price(),
            "gas":      200_000,
        })

        signed  = self.wallet.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[SwapExecutor] USDC→ETH  {amount_usdc} USDC  tx={tx_hash.hex()[:18]}…")
        return tx_hash.hex()

    def estimate_gas_usd(self, gas: int = 200_000) -> float:
        """Rough gas cost estimate in USD (uses current gas price + $2500/ETH fallback)."""
        try:
            from engine.market_data import MarketData
            eth_price = MarketData().get_price()
        except Exception:
            eth_price = 2500.0
        gas_wei   = self.w3.eth.gas_price * gas
        gas_eth   = self.w3.from_wei(gas_wei, "ether")
        return float(gas_eth) * eth_price
