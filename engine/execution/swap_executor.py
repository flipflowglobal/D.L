# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
Uniswap V3 Swap Executor (Ethereum Mainnet) — EIP-1559 upgrade.

Executes exact-input swaps via the Uniswap V3 SwapRouter02.
Supports:
  • ETH  → ERC-20  (wraps ETH as msg.value)
  • ERC-20 → ERC-20 (requires prior token approval)

All transactions use EIP-1559 (type-2) fee fields:
  maxPriorityFeePerGas + maxFeePerGas instead of legacy gasPrice.

Mainnet addresses:
  SwapRouter02      : 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45
  SwapRouter (V1)   : 0xE592427A0AEce92De3Edee1F18E0157C05861564
  WETH              : 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
  USDC              : 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
"""

import time
from typing import Optional
from web3 import Web3

from vault.wallet_config import WalletConfig

# SwapRouter02 — preferred (no deadline param, no permit2 required)
SWAP_ROUTER02  = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
# SwapRouter V1 — kept as fallback
SWAP_ROUTER_V1 = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
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

# SwapRouter02 ABI — exactInputSingle without `deadline` field
ROUTER02_ABI = [
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
    Signs and broadcasts EIP-1559 swaps on Uniswap V3 SwapRouter02.

    Uses maxFeePerGas / maxPriorityFeePerGas (EIP-1559 type-2 txs) instead
    of the legacy gasPrice field, which is cheaper and more predictable.

    Usage:
        executor = SwapExecutor(wallet, rpc_url)
        tx_hash = executor.swap_eth_to_usdc(amount_eth=0.1, slippage=0.005)
    """

    DEFAULT_FEE      = 500      # 0.05 % pool (cheapest ETH/USDC pool)
    DEADLINE_SECONDS = 300      # 5-minute deadline (kept for legacy compat)

    def __init__(self, wallet: WalletConfig, rpc_url: str):
        self.wallet = wallet
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError(f"Web3 connection failed: {rpc_url}")

        # Prefer SwapRouter02; the contract address is the same on all EVM chains
        self.router_address = SWAP_ROUTER02
        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(SWAP_ROUTER02),
            abi=ROUTER02_ABI,
        )

        # Lazy-import AlchemyClient for fee estimation
        try:
            from engine.mainnet.alchemy_client import AlchemyClient
            self._alchemy = AlchemyClient(rpc_url)
        except Exception:
            self._alchemy = None

    # ── EIP-1559 fee helpers ──────────────────────────────────────────────────

    def _eip1559_fees(self) -> dict:
        """Return maxFeePerGas and maxPriorityFeePerGas for a type-2 tx."""
        if self._alchemy:
            try:
                fees = self._alchemy.get_eip1559_fees()
                return {
                    "maxFeePerGas":         fees.max_fee_per_gas_wei,
                    "maxPriorityFeePerGas": fees.max_priority_fee_wei,
                }
            except Exception:
                pass
        # Fallback: derive from web3
        try:
            block    = self.w3.eth.get_block("latest")
            base_fee = int(block.get("baseFeePerGas", 20 * int(1e9)))
            priority = int(self.w3.eth.max_priority_fee)
            return {
                "maxFeePerGas":         base_fee * 2 + priority,
                "maxPriorityFeePerGas": priority,
            }
        except Exception:
            return {
                "maxFeePerGas":         int(50e9),   # 50 gwei hard-cap
                "maxPriorityFeePerGas": int(1.5e9),
            }

    def _min_out(self, amount: int, slippage: float) -> int:
        return int(amount * (1 - slippage))

    def _ensure_approval(self, token_address: str, amount_wei: int) -> None:
        """Approve SwapRouter02 to spend `amount_wei` of `token_address` if needed."""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_APPROVE_ABI,
        )
        current = token.functions.allowance(
            self.wallet.account.address,
            Web3.to_checksum_address(self.router_address),
        ).call()
        if current >= amount_wei:
            return

        nonce    = self.w3.eth.get_transaction_count(self.wallet.account.address, "pending")
        fees     = self._eip1559_fees()
        tx = token.functions.approve(
            Web3.to_checksum_address(self.router_address),
            2**256 - 1,
        ).build_transaction({
            "from":    self.wallet.account.address,
            "nonce":   nonce,
            "gas":     60_000,
            "type":    2,
            "chainId": self.w3.eth.chain_id,
            **fees,
        })
        signed = self.wallet.account.sign_transaction(tx)
        self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[SwapExecutor] Approved {token_address[:8]}… for SwapRouter02")

    # ── public swap methods ───────────────────────────────────────────────────

    def swap_eth_to_usdc(
        self,
        amount_eth: float,
        slippage: float = 0.005,
        expected_usdc: Optional[float] = None,
    ) -> str:
        """
        Sell `amount_eth` ETH for USDC via EIP-1559 tx on SwapRouter02.
        Returns hex tx hash.
        """
        amount_in = self.w3.to_wei(amount_eth, "ether")
        min_out   = int(expected_usdc * 1e6 * (1 - slippage)) if expected_usdc else 0

        params = {
            "tokenIn":           Web3.to_checksum_address(WETH_ADDRESS),
            "tokenOut":          Web3.to_checksum_address(USDC_ADDRESS),
            "fee":               self.DEFAULT_FEE,
            "recipient":         self.wallet.account.address,
            "amountIn":          amount_in,
            "amountOutMinimum":  min_out,
            "sqrtPriceLimitX96": 0,
        }

        nonce = self.w3.eth.get_transaction_count(self.wallet.account.address, "pending")
        fees  = self._eip1559_fees()
        tx    = self.router.functions.exactInputSingle(params).build_transaction({
            "from":    self.wallet.account.address,
            "value":   amount_in,
            "nonce":   nonce,
            "gas":     200_000,
            "type":    2,
            "chainId": self.w3.eth.chain_id,
            **fees,
        })

        signed  = self.wallet.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[SwapExecutor] ETH→USDC  {amount_eth} ETH  tx={tx_hash.hex()[:18]}…")
        return tx_hash.hex()

    def swap_usdc_to_eth(
        self,
        amount_usdc: float,
        slippage: float = 0.005,
        expected_eth: Optional[float] = None,
    ) -> str:
        """
        Sell `amount_usdc` USDC for ETH (WETH) via EIP-1559 tx on SwapRouter02.
        Returns hex tx hash.
        """
        amount_in = int(amount_usdc * 1e6)
        min_out   = (
            self.w3.to_wei(expected_eth * (1 - slippage), "ether")
            if expected_eth else 0
        )

        self._ensure_approval(USDC_ADDRESS, amount_in)

        params = {
            "tokenIn":           Web3.to_checksum_address(USDC_ADDRESS),
            "tokenOut":          Web3.to_checksum_address(WETH_ADDRESS),
            "fee":               self.DEFAULT_FEE,
            "recipient":         self.wallet.account.address,
            "amountIn":          amount_in,
            "amountOutMinimum":  min_out,
            "sqrtPriceLimitX96": 0,
        }

        nonce = self.w3.eth.get_transaction_count(self.wallet.account.address, "pending")
        fees  = self._eip1559_fees()
        tx    = self.router.functions.exactInputSingle(params).build_transaction({
            "from":    self.wallet.account.address,
            "value":   0,
            "nonce":   nonce,
            "gas":     200_000,
            "type":    2,
            "chainId": self.w3.eth.chain_id,
            **fees,
        })

        signed  = self.wallet.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[SwapExecutor] USDC→ETH  {amount_usdc} USDC  tx={tx_hash.hex()[:18]}…")
        return tx_hash.hex()

    def estimate_gas_usd(self, gas: int = 200_000) -> float:
        """Rough gas cost estimate in USD using EIP-1559 maxFeePerGas."""
        try:
            from engine.market_data import MarketData
            eth_price = MarketData().get_price()
        except Exception:
            eth_price = 2500.0
        fees    = self._eip1559_fees()
        gas_wei = fees["maxFeePerGas"] * gas
        gas_eth = gas_wei / 1e18
        return float(gas_eth) * eth_price
