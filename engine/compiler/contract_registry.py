"""
engine/compiler/contract_registry.py
======================================
Registry of flash-loan Solidity contracts with per-network address injection.

Supported contracts
-------------------
FlashLoanArbitrage   — simple Aave V3 flash-loan arb (UniV3 + SushiV2)
NexusFlashReceiver   — advanced multi-DEX receiver (UniV3/SushiV2/Curve/Balancer/Camelot)

Supported networks
------------------
mainnet  (chain_id=1)
sepolia  (chain_id=11155111)
arbitrum (chain_id=42161)
base     (chain_id=8453)
polygon  (chain_id=137)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ── Well-known contract addresses per network ─────────────────────────────────

NETWORK_ADDRESSES: dict[int, dict[str, str]] = {
    # Ethereum Mainnet
    1: {
        "aave_pool":    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        "uni_v3_router":"0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "sushi_router": "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
        "weth":         "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "usdc":         "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "balancer":     "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    },
    # Sepolia Testnet
    11155111: {
        "aave_pool":    "0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951",
        "uni_v3_router":"0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "sushi_router": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
        "weth":         "0xC558DBdd856501FCd9aaF1E62eae57A9F0629a3c",
        "usdc":         "0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8",
        "balancer":     "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    },
    # Arbitrum One
    42161: {
        "aave_pool":    "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "uni_v3_router":"0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "sushi_router": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
        "weth":         "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "usdc":         "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
        "balancer":     "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        "camelot":      "0xc873fEcbd354f5A56E00E710B90EF4201db2448d",
    },
    # Base
    8453: {
        "aave_pool":    "0xA238Dd8c259C2f1BED7b3c04Ef7C3Ac6Fbb5DC43",
        "uni_v3_router":"0x2626664c2603336E57B271c5C0b26F421741e481",
        "weth":         "0x4200000000000000000000000000000000000006",
        "usdc":         "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "balancer":     "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    },
    # Polygon
    137: {
        "aave_pool":    "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "uni_v3_router":"0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "sushi_router": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
        "weth":         "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        "usdc":         "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "balancer":     "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    },
}

NETWORK_NAMES: dict[int, str] = {
    1:        "mainnet",
    11155111: "sepolia",
    42161:    "arbitrum",
    8453:     "base",
    137:      "polygon",
}

# ── Contract source files on disk ─────────────────────────────────────────────

_SOL_DIR = Path(__file__).parent.parent.parent / "contracts"


def _load_source(filename: str) -> str:
    """Load Solidity source from contracts/ directory."""
    path = _SOL_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Contract source not found: {path}")
    return path.read_text()


# ── Registry entries ──────────────────────────────────────────────────────────

class ContractSpec:
    """Specification for a compilable flash-loan contract."""

    def __init__(
        self,
        name: str,
        source_file: str,
        solc_version: str,
        optimize_runs: int,
        abi: list[dict[str, Any]],
        constructor_args_fn,  # callable(chain_id: int) -> list
        description: str = "",
    ):
        self.name             = name
        self.source_file      = source_file
        self.solc_version     = solc_version
        self.optimize_runs    = optimize_runs
        self.abi              = abi
        self._constructor_fn  = constructor_args_fn
        self.description      = description

    def source(self) -> str:
        return _load_source(self.source_file)

    def constructor_args(self, chain_id: int) -> list:
        return self._constructor_fn(chain_id)

    def network_addrs(self, chain_id: int) -> dict[str, str]:
        addrs = NETWORK_ADDRESSES.get(chain_id)
        if addrs is None:
            raise ValueError(
                f"Chain {chain_id} not in registry. "
                f"Supported: {sorted(NETWORK_ADDRESSES.keys())}"
            )
        return addrs


# ── ABI definitions ───────────────────────────────────────────────────────────

_FLASH_LOAN_ARBITRAGE_ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {"internalType": "address", "name": "_pool",         "type": "address"},
            {"internalType": "uint256", "name": "_minProfitWei", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "constructor",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "asset",     "type": "address"},
            {"internalType": "uint256", "name": "amount",    "type": "uint256"},
            {"internalType": "uint256", "name": "premium",   "type": "uint256"},
            {"internalType": "address", "name": "initiator", "type": "address"},
            {"internalType": "bytes",   "name": "params",    "type": "bytes"},
        ],
        "name": "executeOperation",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amount",       "type": "uint256"},
            {"internalType": "uint8",   "name": "direction",    "type": "uint8"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "uint256", "name": "deadline",     "type": "uint256"},
        ],
        "name": "initiate",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "_minProfitWei", "type": "uint256"}],
        "name": "setMinProfit",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "token", "type": "address"}],
        "name": "withdraw",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "withdrawEth",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "owner",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "minProfitWei",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "token",    "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "borrowed", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "repaid",   "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "profit",   "type": "uint256"},
            {"indexed": False, "internalType": "uint8",   "name": "direction","type": "uint8"},
        ],
        "name": "ArbitrageExecuted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "to",     "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount", "type": "uint256"},
            {"indexed": False, "internalType": "address", "name": "token",  "type": "address"},
        ],
        "name": "ProfitWithdrawn",
        "type": "event",
    },
]

_NEXUS_FLASH_RECEIVER_ABI: list[dict[str, Any]] = [
    {
        "inputs": [{"internalType": "address", "name": "_aavePool", "type": "address"}],
        "stateMutability": "nonpayable",
        "type": "constructor",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "asset",     "type": "address"},
            {"internalType": "uint256", "name": "amount",    "type": "uint256"},
            {"internalType": "uint256", "name": "premium",   "type": "uint256"},
            {"internalType": "address", "name": "initiator", "type": "address"},
            {"internalType": "bytes",   "name": "params",    "type": "bytes"},
        ],
        "name": "executeOperation",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "asset",     "type": "address"},
            {"internalType": "uint256", "name": "amount",    "type": "uint256"},
            {"internalType": "bytes",   "name": "steps",     "type": "bytes"},
            {"internalType": "uint256", "name": "minProfit", "type": "uint256"},
        ],
        "name": "initiate",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "token", "type": "address"}],
        "name": "withdraw",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "withdrawEth",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "owner",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "aavePool",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",    "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "borrowed", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "profit",   "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "steps",    "type": "uint256"},
        ],
        "name": "FlashExecuted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "token",  "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "to",     "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "ProfitWithdrawn",
        "type": "event",
    },
]


# ── Registry ──────────────────────────────────────────────────────────────────

REGISTRY: dict[str, ContractSpec] = {
    "FlashLoanArbitrage": ContractSpec(
        name          = "FlashLoanArbitrage",
        source_file   = "FlashLoanArbitrage.sol",
        solc_version  = "0.8.20",
        optimize_runs = 1_000_000,
        abi           = _FLASH_LOAN_ARBITRAGE_ABI,
        constructor_args_fn = lambda chain_id: [
            NETWORK_ADDRESSES[chain_id]["aave_pool"],
            # Default minProfitWei = 0.001 ETH
            1_000_000_000_000_000,
        ],
        description = (
            "Aave V3 flash-loan arbitrage: borrows WETH, swaps across "
            "Uniswap V3 and SushiSwap V2, repays Aave, keeps profit."
        ),
    ),
    "NexusFlashReceiver": ContractSpec(
        name          = "NexusFlashReceiver",
        source_file   = "NexusFlashReceiver.sol",
        solc_version  = "0.8.20",
        optimize_runs = 1_000_000,
        abi           = _NEXUS_FLASH_RECEIVER_ABI,
        constructor_args_fn = lambda chain_id: [
            NETWORK_ADDRESSES[chain_id]["aave_pool"],
        ],
        description = (
            "Multi-DEX Aave V3 flash-loan receiver: up to 8 sequential "
            "swap steps across UniV3, SushiV2, Curve, Balancer, Camelot."
        ),
    ),
}


def get(name: str) -> ContractSpec:
    """Return a ContractSpec by name, raising KeyError if not found."""
    if name not in REGISTRY:
        raise KeyError(
            f"Contract '{name}' not in registry. Available: {list(REGISTRY.keys())}"
        )
    return REGISTRY[name]


def list_contracts() -> list[str]:
    """Return names of all registered contracts."""
    return list(REGISTRY.keys())


def network_name(chain_id: int) -> str:
    """Return human-readable network name for a chain_id."""
    return NETWORK_NAMES.get(chain_id, f"chain-{chain_id}")
