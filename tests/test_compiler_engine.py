"""
tests/test_compiler_engine.py
================================
Offline unit tests for the NexusSolidityEngine, contract registry,
and flash-loan calldata builders.

All web3 / RPC / solcx calls are mocked — no live network required.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Test constants ────────────────────────────────────────────────────────────

DUMMY_KEY     = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DUMMY_ADDR    = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
WETH          = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC          = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
UNI_ROUTER    = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
SUSHI_ROUTER  = "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F"
AAVE_POOL_SEP = "0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951"
AAVE_POOL_MN  = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

CHAIN_SEPOLIA = 11155111
CHAIN_MAINNET = 1


# ═════════════════════════════════════════════════════════════════════════════
# ContractRegistry tests
# ═════════════════════════════════════════════════════════════════════════════

class TestContractRegistry:

    def test_list_contains_expected_contracts(self):
        from engine.compiler import contract_registry
        names = contract_registry.list_contracts()
        assert "FlashLoanArbitrage" in names
        assert "NexusFlashReceiver" in names

    def test_get_returns_spec(self):
        from engine.compiler.contract_registry import get, ContractSpec
        spec = get("FlashLoanArbitrage")
        assert isinstance(spec, ContractSpec)
        assert spec.name == "FlashLoanArbitrage"

    def test_get_unknown_raises_key_error(self):
        from engine.compiler.contract_registry import get
        with pytest.raises(KeyError):
            get("NonExistentContract")

    def test_flash_loan_arbitrage_abi_has_initiate(self):
        from engine.compiler.contract_registry import get
        spec = get("FlashLoanArbitrage")
        fn_names = [e["name"] for e in spec.abi if e.get("type") == "function"]
        assert "initiate" in fn_names

    def test_nexus_flash_receiver_abi_has_initiate(self):
        from engine.compiler.contract_registry import get
        spec = get("NexusFlashReceiver")
        fn_names = [e["name"] for e in spec.abi if e.get("type") == "function"]
        assert "initiate" in fn_names

    def test_nexus_flash_receiver_abi_has_withdraw(self):
        from engine.compiler.contract_registry import get
        spec = get("NexusFlashReceiver")
        fn_names = [e["name"] for e in spec.abi if e.get("type") == "function"]
        assert "withdraw" in fn_names

    def test_flash_loan_arbitrage_constructor_sepolia(self):
        from engine.compiler.contract_registry import get
        spec = get("FlashLoanArbitrage")
        args = spec.constructor_args(CHAIN_SEPOLIA)
        assert args[0] == AAVE_POOL_SEP
        assert isinstance(args[1], int)

    def test_flash_loan_arbitrage_constructor_mainnet(self):
        from engine.compiler.contract_registry import get
        spec = get("FlashLoanArbitrage")
        args = spec.constructor_args(CHAIN_MAINNET)
        assert args[0] == AAVE_POOL_MN

    def test_nexus_receiver_constructor_sepolia(self):
        from engine.compiler.contract_registry import get
        spec = get("NexusFlashReceiver")
        args = spec.constructor_args(CHAIN_SEPOLIA)
        assert args[0] == AAVE_POOL_SEP

    def test_nexus_receiver_constructor_mainnet(self):
        from engine.compiler.contract_registry import get
        spec = get("NexusFlashReceiver")
        args = spec.constructor_args(CHAIN_MAINNET)
        assert args[0] == AAVE_POOL_MN

    def test_network_addresses_mainnet_weth(self):
        from engine.compiler.contract_registry import NETWORK_ADDRESSES
        assert NETWORK_ADDRESSES[1]["weth"] == WETH

    def test_network_addresses_sepolia_aave(self):
        from engine.compiler.contract_registry import NETWORK_ADDRESSES
        assert NETWORK_ADDRESSES[CHAIN_SEPOLIA]["aave_pool"] == AAVE_POOL_SEP

    def test_network_addresses_arbitrum_present(self):
        from engine.compiler.contract_registry import NETWORK_ADDRESSES
        assert 42161 in NETWORK_ADDRESSES
        assert "aave_pool" in NETWORK_ADDRESSES[42161]

    def test_network_addresses_base_present(self):
        from engine.compiler.contract_registry import NETWORK_ADDRESSES
        assert 8453 in NETWORK_ADDRESSES

    def test_network_addresses_polygon_present(self):
        from engine.compiler.contract_registry import NETWORK_ADDRESSES
        assert 137 in NETWORK_ADDRESSES

    def test_network_name_sepolia(self):
        from engine.compiler.contract_registry import network_name
        assert network_name(CHAIN_SEPOLIA) == "sepolia"

    def test_network_name_mainnet(self):
        from engine.compiler.contract_registry import network_name
        assert network_name(1) == "mainnet"

    def test_network_name_unknown_chain(self):
        from engine.compiler.contract_registry import network_name
        assert "999" in network_name(999)

    def test_unsupported_chain_raises(self):
        from engine.compiler.contract_registry import get
        spec = get("NexusFlashReceiver")
        with pytest.raises(ValueError):
            spec.network_addrs(9999)

    def test_source_file_loads(self):
        from engine.compiler.contract_registry import get
        spec = get("FlashLoanArbitrage")
        src = spec.source()
        assert "FlashLoanArbitrage" in src
        assert "pragma solidity" in src

    def test_nexus_source_file_loads(self):
        from engine.compiler.contract_registry import get
        spec = get("NexusFlashReceiver")
        src = spec.source()
        assert "NexusFlashReceiver" in src
        assert "pragma solidity" in src


# ═════════════════════════════════════════════════════════════════════════════
# CompileResult tests
# ═════════════════════════════════════════════════════════════════════════════

class TestCompileResult:

    def _make_result(self, bytecode: str = "deadbeef") -> "CompileResult":
        from engine.compiler.solidity_engine import CompileResult
        from web3 import Web3
        return CompileResult(
            contract_name = "TestContract",
            chain_id      = CHAIN_SEPOLIA,
            solc_version  = "0.8.20",
            bytecode_hex  = bytecode,
            abi           = [],
            source_hash   = Web3.keccak(b"source").hex(),
            bytecode_hash = Web3.keccak(bytes.fromhex(bytecode)).hex(),
            layer         = 1,
            elapsed_s     = 0.5,
            optimize_runs = 200,
        )

    def test_byte_count(self):
        r = self._make_result("deadbeef")
        assert r.byte_count == 4

    def test_bytecode_0x(self):
        r = self._make_result("deadbeef")
        assert r.bytecode_0x == "0xdeadbeef"

    def test_to_dict_has_required_keys(self):
        r = self._make_result()
        d = r.to_dict()
        for key in ("contract_name", "chain_id", "bytecode_hex", "abi",
                    "source_hash", "bytecode_hash", "layer", "byte_count"):
            assert key in d


# ═════════════════════════════════════════════════════════════════════════════
# NexusSolidityEngine — compile via layer 1 (solcx mocked)
# ═════════════════════════════════════════════════════════════════════════════

def _make_mock_solcx_output(name: str, abi: list, bytecode: str) -> dict:
    return {f"<stdin>:{name}": {"abi": abi, "bin": bytecode}}


class TestNexusSolidityEngineCompile:

    def setup_method(self):
        from engine.compiler.solidity_engine import NexusSolidityEngine
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self.engine  = NexusSolidityEngine(build_dir=Path(self._tmpdir))

    def _spec(self, name: str = "FlashLoanArbitrage"):
        from engine.compiler.contract_registry import get
        return get(name)

    def test_compile_uses_solcx_layer1(self):
        """Engine succeeds via layer 1 when solcx returns valid output."""
        spec     = self._spec()
        fake_abi = [{"type": "constructor"}]
        fake_bc  = "deadbeef" * 16

        mock_out = _make_mock_solcx_output("FlashLoanArbitrage", fake_abi, fake_bc)

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = ["0.8.20"]
            solcx_mock.set_solc_version.return_value = None
            solcx_mock.compile_source.return_value   = mock_out

            result = self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)

        assert result.bytecode_hex == fake_bc
        assert result.layer == 1
        assert result.chain_id == CHAIN_SEPOLIA

    def test_compile_installs_solc_if_missing(self):
        """Engine calls install_solc when version not yet installed."""
        spec     = self._spec()
        fake_bc  = "cafebabe" * 16
        mock_out = _make_mock_solcx_output("FlashLoanArbitrage", [], fake_bc)

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = []
            solcx_mock.install_solc.return_value    = None
            solcx_mock.set_solc_version.return_value = None
            solcx_mock.compile_source.return_value   = mock_out

            result = self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)

        solcx_mock.install_solc.assert_called_once_with("0.8.20", show_progress=False)
        assert result.layer == 1

    def test_compile_cache_hit_skips_solcx(self):
        """Second compile with same source returns cached result without calling solcx."""
        spec     = self._spec()
        fake_abi = [{"type": "constructor"}]
        fake_bc  = "aabbccdd" * 16
        mock_out = _make_mock_solcx_output("FlashLoanArbitrage", fake_abi, fake_bc)

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = ["0.8.20"]
            solcx_mock.set_solc_version.return_value = None
            solcx_mock.compile_source.return_value   = mock_out

            # First compile — hits layer 1
            r1 = self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)
            # Second compile — should hit cache
            r2 = self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)

        assert r1.bytecode_hex == r2.bytecode_hex
        # solcx.compile_source should only be called once
        assert solcx_mock.compile_source.call_count == 1

    def test_compile_force_bypasses_cache(self):
        """force=True always recompiles even when cache is valid."""
        spec     = self._spec()
        fake_bc  = "11223344" * 16
        mock_out = _make_mock_solcx_output("FlashLoanArbitrage", [], fake_bc)

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = ["0.8.20"]
            solcx_mock.set_solc_version.return_value = None
            solcx_mock.compile_source.return_value   = mock_out

            self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)
            self.engine.compile(spec, chain_id=CHAIN_SEPOLIA, force=True)

        assert solcx_mock.compile_source.call_count == 2

    def test_compile_falls_to_layer4_embedded_when_all_fail(self):
        """When layers 1-3 fail, engine uses embedded verified bytecode."""
        spec = self._spec("FlashLoanArbitrage")

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = ["0.8.20"]
            solcx_mock.set_solc_version.return_value = None
            # Return empty bin to simulate failure
            solcx_mock.compile_source.return_value = {
                f"<stdin>:FlashLoanArbitrage": {"abi": [], "bin": ""}
            }

            with patch("requests.post", side_effect=Exception("network unavailable")):
                result = self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)

        assert result.layer == 4
        assert len(result.bytecode_hex) > 0

    def test_compile_saves_abi_json(self):
        """After compilation, ABI JSON file is written to build dir."""
        spec     = self._spec()
        fake_abi = [{"type": "constructor", "inputs": []}]
        fake_bc  = "deadbeef" * 16
        mock_out = _make_mock_solcx_output("FlashLoanArbitrage", fake_abi, fake_bc)

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = ["0.8.20"]
            solcx_mock.set_solc_version.return_value = None
            solcx_mock.compile_source.return_value   = mock_out

            self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)

        abi_path = Path(self._tmpdir) / "FlashLoanArbitrage.abi.json"
        assert abi_path.exists()
        loaded = json.loads(abi_path.read_text())
        assert loaded == fake_abi

    def test_compile_saves_bin_file(self):
        """After compilation, bytecode .bin file is written to build dir."""
        spec     = self._spec()
        fake_bc  = "cafebabe" * 16
        mock_out = _make_mock_solcx_output("FlashLoanArbitrage", [], fake_bc)

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = ["0.8.20"]
            solcx_mock.set_solc_version.return_value = None
            solcx_mock.compile_source.return_value   = mock_out

            self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)

        bin_path = Path(self._tmpdir) / "FlashLoanArbitrage.bin"
        assert bin_path.exists()
        assert bin_path.read_text() == fake_bc

    def test_compile_nexus_receiver(self):
        """NexusFlashReceiver compiles successfully (layer 1 mock)."""
        spec     = self._spec("NexusFlashReceiver")
        fake_abi = [{"type": "constructor", "inputs": [
            {"name": "_aavePool", "type": "address", "internalType": "address"}
        ]}]
        fake_bc  = "beefcafe" * 20
        mock_out = _make_mock_solcx_output("NexusFlashReceiver", fake_abi, fake_bc)

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = ["0.8.20"]
            solcx_mock.set_solc_version.return_value = None
            solcx_mock.compile_source.return_value   = mock_out

            result = self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)

        assert result.contract_name == "NexusFlashReceiver"
        assert result.bytecode_hex == fake_bc

    def test_list_cached_after_compile(self):
        """list_cached() returns one entry after a successful compile."""
        spec     = self._spec()
        fake_bc  = "aabbccdd" * 12
        mock_out = _make_mock_solcx_output("FlashLoanArbitrage", [], fake_bc)

        with patch.dict("sys.modules", {"solcx": MagicMock()}):
            import sys
            solcx_mock = sys.modules["solcx"]
            solcx_mock.get_installed_solc_versions.return_value = ["0.8.20"]
            solcx_mock.set_solc_version.return_value = None
            solcx_mock.compile_source.return_value   = mock_out

            self.engine.compile(spec, chain_id=CHAIN_SEPOLIA)

        cached = self.engine.list_cached()
        assert len(cached) >= 1
        names = [c["contract"] for c in cached]
        assert "FlashLoanArbitrage" in names


# ═════════════════════════════════════════════════════════════════════════════
# NexusSolidityEngine — bytecode integrity (verify)
# ═════════════════════════════════════════════════════════════════════════════

class TestBytecodeIntegrity:

    def setup_method(self):
        from engine.compiler.solidity_engine import NexusSolidityEngine
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self.engine  = NexusSolidityEngine(build_dir=Path(self._tmpdir))

    def _make_result(self, bc: str = "deadbeef" * 4):
        from engine.compiler.solidity_engine import CompileResult
        from web3 import Web3
        return CompileResult(
            contract_name = "TestContract",
            chain_id      = CHAIN_SEPOLIA,
            solc_version  = "0.8.20",
            bytecode_hex  = bc,
            abi           = [],
            source_hash   = Web3.keccak(b"src").hex(),
            bytecode_hash = Web3.keccak(bytes.fromhex(bc)).hex(),
            layer         = 1,
            elapsed_s     = 0.1,
            optimize_runs = 200,
        )

    def test_verify_valid_bytecode(self):
        result = self._make_result()
        assert self.engine.verify(result) is True

    def test_verify_tampered_bytecode_fails(self):
        from engine.compiler.solidity_engine import CompileResult
        from web3 import Web3
        bc = "deadbeef" * 4
        result = CompileResult(
            contract_name = "TestContract",
            chain_id      = CHAIN_SEPOLIA,
            solc_version  = "0.8.20",
            bytecode_hex  = "cafecafe" * 4,  # tampered!
            abi           = [],
            source_hash   = Web3.keccak(b"src").hex(),
            bytecode_hash = Web3.keccak(bytes.fromhex(bc)).hex(),  # original hash
            layer         = 1,
            elapsed_s     = 0.0,
            optimize_runs = 200,
        )
        assert self.engine.verify(result) is False

    def test_source_hash_is_keccak256(self):
        from web3 import Web3
        src  = b"pragma solidity ^0.8.20;"
        h    = Web3.keccak(src).hex()
        assert h.startswith("0x") or len(h) == 64

    def test_bytecode_hash_changes_on_different_bytecode(self):
        r1 = self._make_result("deadbeef" * 4)
        r2 = self._make_result("cafebabe" * 4)
        assert r1.bytecode_hash != r2.bytecode_hash


# ═════════════════════════════════════════════════════════════════════════════
# SwapStep and DEX constants
# ═════════════════════════════════════════════════════════════════════════════

class TestSwapStep:

    def test_dex_constants(self):
        from engine.compiler.calldata_builder import DEX
        assert DEX.UNI_V3   == 0
        assert DEX.SUSHI_V2 == 1
        assert DEX.CURVE    == 2
        assert DEX.BALANCER == 3
        assert DEX.CAMELOT  == 4

    def test_swap_step_defaults(self):
        from engine.compiler.calldata_builder import SwapStep, DEX
        step = SwapStep(
            dex=DEX.UNI_V3,
            router=UNI_ROUTER,
            token_in=WETH,
            token_out=USDC,
        )
        assert step.fee == 3000
        assert step.amount_out_min == 0
        assert step.curve_i == 0
        assert step.curve_j == 1

    def test_swap_step_resolved_deadline_default(self):
        from engine.compiler.calldata_builder import SwapStep, DEX
        step = SwapStep(dex=DEX.UNI_V3, router=UNI_ROUTER,
                        token_in=WETH, token_out=USDC, deadline=0)
        dl = step.resolved_deadline()
        assert dl > int(time.time())
        assert dl <= int(time.time()) + 130

    def test_swap_step_resolved_deadline_explicit(self):
        from engine.compiler.calldata_builder import SwapStep, DEX
        explicit = int(time.time()) + 300
        step = SwapStep(dex=DEX.UNI_V3, router=UNI_ROUTER,
                        token_in=WETH, token_out=USDC, deadline=explicit)
        assert step.resolved_deadline() == explicit

    def test_swap_step_to_tuple_length(self):
        from engine.compiler.calldata_builder import SwapStep, DEX
        step = SwapStep(dex=DEX.UNI_V3, router=UNI_ROUTER,
                        token_in=WETH, token_out=USDC, fee=500)
        tup = step.to_tuple()
        assert len(tup) == 10  # 10 fields

    def test_swap_step_to_tuple_balancer_pool_id_bytes32(self):
        from engine.compiler.calldata_builder import SwapStep, DEX
        step = SwapStep(dex=DEX.BALANCER, router=DUMMY_ADDR,
                        token_in=WETH, token_out=USDC,
                        balancer_pool_id=b"\xAB\xCD" + b"\x00" * 30)
        tup = step.to_tuple()
        assert isinstance(tup[6], bytes)
        assert len(tup[6]) == 32


# ═════════════════════════════════════════════════════════════════════════════
# encode_steps
# ═════════════════════════════════════════════════════════════════════════════

class TestEncodeSteps:

    def test_encode_single_step_returns_bytes(self):
        from engine.compiler.calldata_builder import SwapStep, DEX, encode_steps
        steps = [
            SwapStep(dex=DEX.UNI_V3, router=UNI_ROUTER,
                     token_in=WETH, token_out=USDC, fee=3000)
        ]
        encoded = encode_steps(steps)
        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

    def test_encode_two_steps_longer_than_one(self):
        from engine.compiler.calldata_builder import SwapStep, DEX, encode_steps
        one_step = [
            SwapStep(dex=DEX.UNI_V3, router=UNI_ROUTER, token_in=WETH, token_out=USDC)
        ]
        two_steps = [
            SwapStep(dex=DEX.UNI_V3,   router=UNI_ROUTER,   token_in=WETH, token_out=USDC),
            SwapStep(dex=DEX.SUSHI_V2, router=SUSHI_ROUTER, token_in=USDC, token_out=WETH),
        ]
        assert len(encode_steps(two_steps)) > len(encode_steps(one_step))

    def test_encode_empty_list(self):
        from engine.compiler.calldata_builder import encode_steps
        encoded = encode_steps([])
        assert isinstance(encoded, bytes)

    def test_encode_all_dex_types(self):
        """All DEX types can be encoded without error."""
        from engine.compiler.calldata_builder import SwapStep, DEX, encode_steps
        steps = [
            SwapStep(dex=DEX.UNI_V3,   router=UNI_ROUTER,   token_in=WETH, token_out=USDC),
            SwapStep(dex=DEX.SUSHI_V2, router=SUSHI_ROUTER, token_in=USDC, token_out=WETH),
            SwapStep(dex=DEX.CURVE,    router=DUMMY_ADDR,   token_in=WETH, token_out=USDC),
            SwapStep(dex=DEX.BALANCER, router=DUMMY_ADDR,   token_in=WETH, token_out=USDC),
            SwapStep(dex=DEX.CAMELOT,  router=DUMMY_ADDR,   token_in=WETH, token_out=USDC),
        ]
        encoded = encode_steps(steps)
        assert len(encoded) > 0


# ═════════════════════════════════════════════════════════════════════════════
# FlashLoanArbitrageCalldata builder
# ═════════════════════════════════════════════════════════════════════════════

def _make_w3_mock() -> MagicMock:
    """Minimal Web3 mock that supports contract interactions."""
    w3 = MagicMock()
    w3.is_connected.return_value = True
    w3.eth.chain_id = CHAIN_SEPOLIA
    w3.eth.gas_price = 20_000_000_000
    w3.eth.get_transaction_count.return_value = 0

    # Mock fee_history for EIP-1559
    w3.eth.fee_history.return_value = {
        "baseFeePerGas": [int(10e9), int(12e9)]
    }
    w3.to_wei.side_effect = lambda v, u: int(float(v) * 1e9) if u == "gwei" else int(v)
    w3.from_wei.side_effect = lambda v, u: v / 1e18 if u == "ether" else v / 1e9

    # Mock contract and function calls
    mock_fn = MagicMock()
    mock_fn.estimate_gas.return_value   = 350_000
    mock_fn.build_transaction.return_value = {
        "from":    DUMMY_ADDR,
        "nonce":   0,
        "gas":     420_000,
        "gasPrice": 20_000_000_000,
        "chainId": CHAIN_SEPOLIA,
        "to":      "0x1234000000000000000000000000000000000001",
        "data":    b"\x00" * 64,
    }
    mock_fn.encode_abi = MagicMock(return_value=b"\x01" * 36)

    mock_contract = MagicMock()
    mock_contract.functions.initiate.return_value = mock_fn
    mock_contract.encode_abi.return_value          = b"\x01" * 36
    w3.eth.contract.return_value = mock_contract

    return w3


class TestFlashLoanArbitrageCalldata:

    def _builder(self, w3=None):
        from engine.compiler.calldata_builder import FlashLoanArbitrageCalldata
        from engine.compiler.contract_registry import get
        spec = get("FlashLoanArbitrage")
        return FlashLoanArbitrageCalldata(
            contract_address="0x1234000000000000000000000000000000000001",
            abi=spec.abi,
            w3=w3 or _make_w3_mock(),
        )

    def test_build_returns_dict_with_from(self):
        builder = self._builder()
        tx = builder.build(
            sender=DUMMY_ADDR,
            amount_wei=int(1e18),
            direction=0,
            amount_out_min=0,
        )
        assert isinstance(tx, dict)
        assert "from" in tx or "gas" in tx

    def test_build_applies_gas_buffer(self):
        w3      = _make_w3_mock()
        builder = self._builder(w3)

        # estimate_gas returns 350_000; buffer 1.20 → 420_000
        tx = builder.build(
            sender=DUMMY_ADDR,
            amount_wei=int(1e18),
            direction=0,
            amount_out_min=0,
            gas_buffer=1.20,
        )
        # build_transaction was called — gas should be 420_000 (350_000 * 1.2)
        mock_fn = w3.eth.contract.return_value.functions.initiate.return_value
        args, kwargs = mock_fn.build_transaction.call_args
        gas = (args[0] if args else kwargs.get("transaction", {})).get("gas", 0)
        assert gas == int(350_000 * 1.20)

    def test_direction_buy_uni_sell_sushi(self):
        from engine.compiler.calldata_builder import FlashLoanArbitrageCalldata
        assert FlashLoanArbitrageCalldata.BUY_UNI_SELL_SUSHI == 0

    def test_direction_buy_sushi_sell_uni(self):
        from engine.compiler.calldata_builder import FlashLoanArbitrageCalldata
        assert FlashLoanArbitrageCalldata.BUY_SUSHI_SELL_UNI == 1

    def test_encode_params_returns_bytes(self):
        builder = self._builder()
        params = builder.encode_params(
            amount_wei=int(1e18),
            direction=0,
            amount_out_min=0,
        )
        assert isinstance(params, bytes)


# ═════════════════════════════════════════════════════════════════════════════
# NexusFlashCalldataBuilder
# ═════════════════════════════════════════════════════════════════════════════

class TestNexusFlashCalldataBuilder:

    def _builder(self, w3=None):
        from engine.compiler.calldata_builder import NexusFlashCalldataBuilder
        from engine.compiler.contract_registry import get
        spec = get("NexusFlashReceiver")
        return NexusFlashCalldataBuilder(
            contract_address="0x9999000000000000000000000000000000000001",
            abi=spec.abi,
            w3=w3 or _make_w3_mock(),
        )

    def _steps(self):
        from engine.compiler.calldata_builder import SwapStep, DEX
        return [
            SwapStep(dex=DEX.UNI_V3,   router=UNI_ROUTER,   token_in=WETH, token_out=USDC),
            SwapStep(dex=DEX.SUSHI_V2, router=SUSHI_ROUTER, token_in=USDC, token_out=WETH),
        ]

    def test_build_returns_dict(self):
        builder = self._builder()
        tx = builder.build(
            sender=DUMMY_ADDR,
            asset=WETH,
            amount_wei=int(10e18),
            steps=self._steps(),
            min_profit_wei=int(0.001e18),
        )
        assert isinstance(tx, dict)

    def test_estimate_gas_calls_estimate(self):
        w3      = _make_w3_mock()
        builder = self._builder(w3)
        gas = builder.estimate_gas(
            sender=DUMMY_ADDR,
            asset=WETH,
            amount_wei=int(10e18),
            steps=self._steps(),
        )
        assert gas == 350_000

    def test_encode_params_returns_bytes(self):
        builder = self._builder()
        params = builder.encode_params(
            asset=WETH,
            amount_wei=int(5e18),
            steps=self._steps(),
            min_profit_wei=0,
        )
        assert isinstance(params, bytes)

    def test_build_with_zero_steps_encodes(self):
        """Zero steps are allowed at the Python level (Solidity reverts)."""
        builder = self._builder()
        tx = builder.build(
            sender=DUMMY_ADDR,
            asset=WETH,
            amount_wei=int(1e18),
            steps=[],
            min_profit_wei=0,
        )
        assert isinstance(tx, dict)


# ═════════════════════════════════════════════════════════════════════════════
# NexusSolidityEngine — deploy (fully mocked, no real RPC)
# ═════════════════════════════════════════════════════════════════════════════

class TestNexusSolidityEngineDeploy:

    def setup_method(self):
        from engine.compiler.solidity_engine import NexusSolidityEngine
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self.engine  = NexusSolidityEngine(build_dir=Path(self._tmpdir))

    def _make_result(self, chain_id: int = CHAIN_SEPOLIA):
        from engine.compiler.solidity_engine import CompileResult
        from web3 import Web3
        bc = "deadbeef" * 100
        return CompileResult(
            contract_name = "FlashLoanArbitrage",
            chain_id      = chain_id,
            solc_version  = "0.8.20",
            bytecode_hex  = bc,
            abi           = [],
            source_hash   = Web3.keccak(b"src").hex(),
            bytecode_hash = Web3.keccak(bytes.fromhex(bc)).hex(),
            layer         = 1,
            elapsed_s     = 0.5,
            optimize_runs = 1_000_000,
        )

    def test_deploy_chain_mismatch_raises(self):
        from engine.compiler.contract_registry import get
        spec   = get("FlashLoanArbitrage")
        result = self._make_result(chain_id=1)  # mainnet result

        w3 = MagicMock()
        w3.is_connected.return_value = True
        w3.eth.chain_id = CHAIN_SEPOLIA   # but RPC is Sepolia

        with patch("engine.compiler.solidity_engine.Web3") as MockWeb3:
            MockWeb3.HTTPProvider.return_value = MagicMock()
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address.side_effect = lambda a: a

            with pytest.raises(ValueError, match="Chain mismatch"):
                self.engine.deploy(spec, result,
                                   rpc_url="http://rpc", private_key=DUMMY_KEY)

    def test_deploy_saves_address_file(self):
        from engine.compiler.contract_registry import get
        spec   = get("FlashLoanArbitrage")
        result = self._make_result(chain_id=CHAIN_SEPOLIA)

        deployed_addr = "0xAbCd000000000000000000000000000000001234"

        w3      = MagicMock()
        account = MagicMock(address=DUMMY_ADDR)

        w3.is_connected.return_value = True
        w3.eth.chain_id = CHAIN_SEPOLIA
        w3.eth.get_transaction_count.return_value = 0
        w3.to_wei.side_effect = lambda v, u: int(float(v) * 1e9)
        w3.from_wei.return_value = 0.001
        w3.eth.fee_history.return_value = {"baseFeePerGas": [int(10e9)]}

        mock_constructor_fn = MagicMock()
        mock_constructor_fn.estimate_gas.return_value = 2_500_000
        mock_constructor_fn.build_transaction.return_value = {
            "from": DUMMY_ADDR, "nonce": 0, "gas": 3_000_000,
            "gasPrice": int(20e9), "chainId": CHAIN_SEPOLIA, "data": b"",
        }

        mock_contract_cls = MagicMock()
        mock_contract_cls.constructor.return_value = mock_constructor_fn

        signed_tx = MagicMock(raw_transaction=b"\x02" * 300)
        account.sign_transaction.return_value = signed_tx
        w3.eth.account.from_key.return_value  = account

        receipt = MagicMock(contractAddress=deployed_addr, status=1, gasUsed=2_100_000)
        w3.eth.send_raw_transaction.return_value    = b"\xde\xad"
        w3.eth.wait_for_transaction_receipt.return_value = receipt
        w3.eth.contract.return_value = mock_contract_cls

        with patch("engine.compiler.solidity_engine.Web3") as MockWeb3:
            MockWeb3.HTTPProvider.return_value = MagicMock()
            MockWeb3.return_value              = w3
            MockWeb3.to_checksum_address.side_effect = lambda a: a
            MockWeb3.keccak.return_value = b"\x00" * 32

            addr = self.engine.deploy(
                spec, result,
                rpc_url="http://rpc",
                private_key=DUMMY_KEY,
            )

        assert addr == deployed_addr
        addr_file = Path(self._tmpdir) / "FlashLoanArbitrage.address.txt"
        assert addr_file.exists()
        assert addr_file.read_text() == deployed_addr
