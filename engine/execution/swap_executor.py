"""
engine/execution/swap_executor.py
===================================

Uniswap V3 Swap Executor — Ethereum Mainnet (EIP-1559).

Executes exact-input swaps via the Uniswap V3 SwapRouter02.
Supports:
  • ETH  → ERC-20  (wraps ETH as msg.value)
  • ERC-20 → ETH   (requires prior token approval)

EIP-1559 upgrade (replaces legacy gasPrice):
  - Uses maxFeePerGas / maxPriorityFeePerGas from AlchemyClient fee oracle
  - Gas limit auto-estimated via eth_estimateGas (+20 % buffer)
  - Waits for on-chain confirmation and detects reverts
  - Token approvals are confirmed before swap proceeds

Mainnet addresses:
  SwapRouter02 : 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45
  WETH         : 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
  USDC         : 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48

Backwards compatibility:
  The constructor still accepts (WalletConfig, rpc_url) for compatibility
  with trade.py.  Internally it builds an AlchemyClient + TransactionManager
  so all new EIP-1559 features activate automatically.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from web3 import Web3

from vault.wallet_config import WalletConfig
from engine.mainnet.alchemy_client      import AlchemyClient
from engine.mainnet.transaction_manager import TransactionManager

# ── Mainnet contract addresses ────────────────────────────────────────────────

# SwapRouter02 supports both V2 and V3 routing and is the recommended router
SWAP_ROUTER  = os.getenv(
    "UNISWAP_ROUTER_ADDRESS", "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
)
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

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
    Signs and broadcasts Uniswap V3 swaps on Ethereum mainnet.

    Constructor accepts the same (wallet, rpc_url) signature as before
    for backward compatibility with trade.py and the test suite.

    Internally it creates an AlchemyClient + TransactionManager so all
    transactions are EIP-1559 with automatic gas estimation and confirmation.

    Usage:
        executor = SwapExecutor(wallet, rpc_url)
        tx_hash = executor.swap_eth_to_usdc(amount_eth=0.1, slippage=0.005,
                                             expected_usdc=340.0)
        # tx_hash is returned only after the swap is confirmed on-chain
    """

    DEFAULT_FEE      = 500       # 0.05 % pool tier (most liquid ETH/USDC pool)
    DEADLINE_SECONDS = 300       # 5-minute deadline for the swap

    def __init__(self, wallet: WalletConfig, rpc_url: str) -> None:
        self.wallet = wallet

        # Build Alchemy-aware client (works with any HTTPS RPC)
        self._alchemy = AlchemyClient(rpc_url)
        self.w3       = self._alchemy.w3

        self._tx_mgr = TransactionManager(
            client=self._alchemy,
            private_key=wallet.private_key,
            chain_id=int(os.getenv("CHAIN_ID", str(self._alchemy.chain_id))),
        )

        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(SWAP_ROUTER),
            abi=ROUTER_ABI,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _deadline(self) -> int:
        return int(time.time()) + self.DEADLINE_SECONDS

    # ── public swap methods ───────────────────────────────────────────────────

    def swap_eth_to_usdc(
        self,
        amount_eth:    float,
        slippage:      float           = 0.005,
        expected_usdc: Optional[float] = None,
        wait:          bool            = True,
    ) -> str:
        """
        Sell ``amount_eth`` ETH for USDC on Uniswap V3.

        Parameters
        ----------
        amount_eth    : ETH to sell (e.g. 0.05)
        slippage      : max acceptable slippage, decimal (0.005 = 0.5 %)
        expected_usdc : quoted USDC output; used to compute amountOutMinimum.
                        If None, min_out = 0 (any output accepted — risky on mainnet).
        wait          : if True, waits for on-chain confirmation (default True)

        Returns
        -------
        Hex transaction hash string (confirmed if wait=True).
        """
        amount_in = self.w3.to_wei(amount_eth, "ether")

        min_out = (
            int(expected_usdc * 1e6 * (1 - slippage))
            if expected_usdc is not None
            else 0
        )

        params = (
            Web3.to_checksum_address(WETH_ADDRESS),
            Web3.to_checksum_address(USDC_ADDRESS),
            self.DEFAULT_FEE,
            self.wallet.account.address,
            amount_in,
            min_out,
            0,    # no sqrt price limit
        )

        calldata = self.router.encodeABI(fn_name="exactInputSingle", args=[params])

        tx = self._tx_mgr.build_tx(
            to=SWAP_ROUTER,
            value_wei=amount_in,
            data=Web3.to_bytes(hexstr=calldata),
        )

        if wait:
            receipt = self._tx_mgr.send_and_confirm(tx)
            print(
                f"[SwapExecutor] ETH→USDC confirmed  "
                f"{amount_eth} ETH  block={receipt.block_number}  "
                f"gas={receipt.gas_used:,}  tx={receipt.tx_hash[:18]}…"
            )
            return receipt.tx_hash
        else:
            tx_hash = self._tx_mgr.sign_and_send(tx)
            print(f"[SwapExecutor] ETH→USDC sent  {amount_eth} ETH  tx={tx_hash[:18]}…")
            return tx_hash

    def swap_usdc_to_eth(
        self,
        amount_usdc:  float,
        slippage:     float           = 0.005,
        expected_eth: Optional[float] = None,
        wait:         bool            = True,
    ) -> str:
        """
        Sell ``amount_usdc`` USDC for ETH (WETH) on Uniswap V3.

        Parameters
        ----------
        amount_usdc  : USDC to sell (e.g. 170.0)
        slippage     : max acceptable slippage (default 0.5 %)
        expected_eth : quoted ETH output for min-out calculation
        wait         : wait for on-chain confirmation (default True)

        Returns
        -------
        Hex transaction hash string.
        """
        amount_in = int(amount_usdc * 1e6)   # USDC has 6 decimals
        min_out   = (
            self.w3.to_wei(expected_eth * (1 - slippage), "ether")
            if expected_eth is not None
            else 0
        )

        # Ensure USDC allowance before swap
        self._tx_mgr.ensure_approval(USDC_ADDRESS, SWAP_ROUTER, amount_in)

        params = (
            Web3.to_checksum_address(USDC_ADDRESS),
            Web3.to_checksum_address(WETH_ADDRESS),
            self.DEFAULT_FEE,
            self.wallet.account.address,
            amount_in,
            min_out,
            0,
        )

        calldata = self.router.encodeABI(fn_name="exactInputSingle", args=[params])

        tx = self._tx_mgr.build_tx(
            to=SWAP_ROUTER,
            value_wei=0,
            data=Web3.to_bytes(hexstr=calldata),
        )

        if wait:
            receipt = self._tx_mgr.send_and_confirm(tx)
            print(
                f"[SwapExecutor] USDC→ETH confirmed  "
                f"{amount_usdc} USDC  block={receipt.block_number}  "
                f"gas={receipt.gas_used:,}  tx={receipt.tx_hash[:18]}…"
            )
            return receipt.tx_hash
        else:
            tx_hash = self._tx_mgr.sign_and_send(tx)
            print(f"[SwapExecutor] USDC→ETH sent  {amount_usdc} USDC  tx={tx_hash[:18]}…")
            return tx_hash

    def estimate_gas_usd(self, gas: int = 200_000) -> float:
        """
        Estimate the USD cost of a transaction at current EIP-1559 gas prices.

        Uses the live AlchemyClient fee oracle for accurate baseFee + tip.
        Falls back to a conservative estimate if the RPC call fails.
        """
        try:
            from engine.market_data import MarketData
            eth_price = MarketData().get_price()
        except Exception:
            eth_price = 2500.0

        try:
            _, _, max_fee = self._alchemy.get_eip1559_fees()
            gas_eth = float(self.w3.from_wei(max_fee * gas, "ether"))
        except Exception:
            # Fallback: 30 gwei × 200k gas
            gas_eth = float(self.w3.from_wei(30 * 10 ** 9 * gas, "ether"))

        return round(gas_eth * eth_price, 4)
