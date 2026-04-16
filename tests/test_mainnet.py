"""
tests/test_mainnet.py
======================

Offline unit tests for the Alchemy mainnet execution layer.

All tests mock Web3 and HTTP calls — no live network required.
Tests cover:
  - AlchemyClient: URL validation, fee estimation, WebSocket URL derivation
  - TransactionManager: nonce management, EIP-1559 tx building, receipt parsing,
    revert detection, gas bump logic
  - SwapExecutor: construction validation (config path only, no live tx)
  - Config: ALCHEMY_API_KEY → get_rpc_url() derivation
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

ALCHEMY_URL   = "https://eth-mainnet.g.alchemy.com/v2/test_key_abc123"
GENERIC_URL   = "https://mainnet.infura.io/v3/abc"
DUMMY_KEY     = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DUMMY_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def _mock_alchemy_client(rpc_url: str = ALCHEMY_URL):
    """Return an AlchemyClient with a mocked Web3 backend."""
    from engine.mainnet.alchemy_client import AlchemyClient

    with patch("engine.mainnet.alchemy_client.Web3") as MockWeb3:
        # Make Web3(provider).is_connected() return True
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 1
        MockWeb3.return_value = mock_w3
        MockWeb3.to_checksum_address.side_effect = lambda x: x
        MockWeb3.to_wei.side_effect = lambda v, u: int(v * 1e18) if u == "ether" else int(v)
        MockWeb3.from_wei.side_effect = lambda v, u: v / 1e18 if u == "ether" else v / 1e9

        client = AlchemyClient.__new__(AlchemyClient)
        client._rpc_url   = rpc_url
        client._timeout   = 30
        client._is_alchemy = "alchemy.com" in rpc_url
        client._w3        = mock_w3
        return client


# ─────────────────────────────────────────────────────────────────────────────
# AlchemyClient
# ─────────────────────────────────────────────────────────────────────────────

class TestAlchemyClient:

    def test_raises_on_empty_url(self):
        from engine.mainnet.alchemy_client import AlchemyClient
        with pytest.raises(ValueError, match="RPC_URL is required"):
            AlchemyClient("")

    def test_raises_on_non_https_url(self):
        from engine.mainnet.alchemy_client import AlchemyClient
        with pytest.raises(ValueError, match="https://"):
            AlchemyClient("ws://example.com")

    def test_raises_on_failed_connection(self):
        from engine.mainnet.alchemy_client import AlchemyClient
        with patch("engine.mainnet.alchemy_client.Web3") as MockWeb3:
            mock_w3 = MagicMock()
            mock_w3.is_connected.return_value = False
            MockWeb3.return_value = mock_w3
            with pytest.raises(ConnectionError, match="Cannot connect"):
                AlchemyClient(ALCHEMY_URL)

    def test_is_alchemy_true_for_alchemy_url(self):
        client = _mock_alchemy_client(ALCHEMY_URL)
        assert client.is_alchemy() is True

    def test_is_alchemy_false_for_generic_url(self):
        client = _mock_alchemy_client(GENERIC_URL)
        assert client.is_alchemy() is False

    def test_websocket_url_derived_for_alchemy(self):
        client = _mock_alchemy_client(ALCHEMY_URL)
        ws = client.websocket_url
        assert ws is not None
        assert ws.startswith("wss://")
        assert "test_key_abc123" in ws

    def test_websocket_url_none_for_non_alchemy(self):
        client = _mock_alchemy_client(GENERIC_URL)
        assert client.websocket_url is None

    def test_get_eip1559_fees_returns_three_positive_ints(self):
        client = _mock_alchemy_client()
        # Mock pending block with baseFeePerGas
        from web3 import Web3
        client._w3.eth.get_block.return_value = {
            "baseFeePerGas": Web3.to_wei(20, "gwei") if hasattr(Web3, "to_wei") else 20_000_000_000
        }
        client._w3.eth.max_priority_fee = 2_000_000_000  # 2 gwei

        base, priority, max_fee = client.get_eip1559_fees()
        assert isinstance(base, int)
        assert isinstance(priority, int)
        assert isinstance(max_fee, int)
        assert base > 0
        assert priority > 0
        assert max_fee >= base + priority

    def test_get_eip1559_fees_fallback_on_error(self):
        client = _mock_alchemy_client()
        client._w3.eth.get_block.side_effect = Exception("RPC down")
        base, priority, max_fee = client.get_eip1559_fees()
        assert base > 0
        assert priority > 0
        assert max_fee > 0

    def test_estimate_gas_adds_buffer(self):
        client = _mock_alchemy_client()
        client._w3.eth.estimate_gas.return_value = 100_000
        result = client.estimate_gas({"to": "0x0", "value": 0})
        # Should be 100_000 × 1.20 = 120_000
        assert result == 120_000

    def test_estimate_gas_fallback_on_error(self):
        client = _mock_alchemy_client()
        client._w3.eth.estimate_gas.side_effect = Exception("call failed")
        result = client.estimate_gas({"to": "0x0", "value": 0})
        assert result == 300_000

    def test_estimate_gas_respects_max_gas(self):
        client = _mock_alchemy_client()
        client._w3.eth.estimate_gas.return_value = 10_000_000   # huge
        result = client.estimate_gas({"to": "0x0", "value": 0})
        # 10M × 1.2 = 12M — returned as-is (AlchemyClient has no cap; cap is in TxManager)
        assert result == 12_000_000

    def test_get_eth_balance(self):
        from web3 import Web3
        client = _mock_alchemy_client()
        client._w3.eth.get_balance.return_value = Web3.to_wei(1.5, "ether") if hasattr(Web3, "to_wei") else int(1.5 * 1e18)
        client._w3.from_wei = lambda v, u: v / 1e18
        balance = client.get_eth_balance(DUMMY_ADDRESS)
        assert abs(balance - 1.5) < 0.01

    def test_get_nonce_pending(self):
        client = _mock_alchemy_client()
        client._w3.eth.get_transaction_count.return_value = 42
        nonce = client.get_nonce(DUMMY_ADDRESS, pending=True)
        assert nonce == 42


# ─────────────────────────────────────────────────────────────────────────────
# TransactionManager
# ─────────────────────────────────────────────────────────────────────────────

class TestTransactionManager:

    def _make_manager(self):
        from engine.mainnet.transaction_manager import TransactionManager

        # Use a plain MagicMock for the client so we can set chain_id freely
        client = MagicMock()
        client.w3 = MagicMock()
        client.chain_id = 1
        client.get_eip1559_fees.return_value = (
            20_000_000_000,   # 20 gwei base
            2_000_000_000,    # 2 gwei priority
            25_000_000_000,   # 25 gwei max
        )
        client.get_nonce.return_value = 5
        client.estimate_gas.return_value = 200_000

        # Give the mock w3 a realistic account
        from eth_account import Account
        real_account = Account.from_key("0x" + DUMMY_KEY)
        client.w3.eth.account.from_key.return_value = real_account

        mgr = TransactionManager(
            client=client,
            private_key=DUMMY_KEY,
            chain_id=1,
        )
        return mgr

    def test_raises_on_empty_key(self):
        from engine.mainnet.transaction_manager import TransactionManager
        client = _mock_alchemy_client()
        with pytest.raises(ValueError, match="private_key is required"):
            TransactionManager(client=client, private_key="")

    def test_address_derived_from_key(self):
        mgr = self._make_manager()
        # Known address for the all-zeros+1 key (Hardhat account 0)
        assert mgr.address.startswith("0x")
        assert len(mgr.address) == 42

    def test_nonce_increments_per_call(self):
        mgr = self._make_manager()
        mgr._nonce = 10      # set directly to skip RPC
        tx1 = mgr.build_tx(to=DUMMY_ADDRESS)
        tx2 = mgr.build_tx(to=DUMMY_ADDRESS)
        assert tx1["nonce"] == 10
        assert tx2["nonce"] == 11

    def test_build_tx_eip1559_fields(self):
        mgr = self._make_manager()
        mgr._nonce = 0
        tx = mgr.build_tx(to=DUMMY_ADDRESS, value_wei=0)
        assert tx["type"] == "0x2"
        assert "maxFeePerGas" in tx
        assert "maxPriorityFeePerGas" in tx
        assert "gasPrice" not in tx    # must NOT use legacy gas
        assert tx["chainId"] == 1

    def test_build_tx_gas_respects_hard_cap(self):
        mgr = self._make_manager()
        mgr._max_gas = 100_000
        mgr._client.estimate_gas = MagicMock(return_value=999_999)
        mgr._nonce = 0
        tx = mgr.build_tx(to=DUMMY_ADDRESS)
        assert tx["gas"] <= 100_000

    def test_build_tx_value_set(self):
        mgr = self._make_manager()
        mgr._nonce = 0
        tx = mgr.build_tx(to=DUMMY_ADDRESS, value_wei=10 ** 18)
        assert tx["value"] == 10 ** 18

    def test_reset_nonce_clears_cached_nonce(self):
        mgr = self._make_manager()
        mgr._nonce = 99
        mgr.reset_nonce()
        assert mgr._nonce is None

    def test_parse_receipt_success(self):
        from engine.mainnet.transaction_manager import TxReceipt
        mgr = self._make_manager()
        raw = {
            "status":            1,
            "gasUsed":           120_000,
            "gas":               200_000,
            "blockNumber":       19_000_000,
            "effectiveGasPrice": 22_000_000_000,
            "logs":              [],
        }
        receipt = mgr._parse_receipt("0xabc", raw)
        assert isinstance(receipt, TxReceipt)
        assert receipt.status == 1
        assert receipt.gas_used == 120_000
        assert receipt.block_number == 19_000_000

    def test_parse_receipt_revert_raises(self):
        from engine.mainnet.transaction_manager import TransactionReverted
        mgr = self._make_manager()
        raw = {
            "status":            0,
            "gasUsed":           60_000,
            "gas":               200_000,
            "blockNumber":       19_000_000,
            "effectiveGasPrice": 22_000_000_000,
            "logs":              [],
        }
        with pytest.raises(TransactionReverted, match="reverted on-chain"):
            mgr._parse_receipt("0xabc", raw)

    def test_wait_for_receipt_timeout_raises(self):
        from engine.mainnet.transaction_manager import ConfirmationTimeout
        mgr = self._make_manager()
        # get_transaction_receipt always returns None (not mined)
        mgr._w3.eth.get_transaction_receipt = MagicMock(return_value=None)
        with pytest.raises(ConfirmationTimeout):
            mgr.wait_for_receipt("0xdeadbeef", timeout=1)

    def test_wait_for_receipt_success(self):
        from engine.mainnet.transaction_manager import TxReceipt
        mgr = self._make_manager()
        mgr._w3.eth.get_transaction_receipt = MagicMock(return_value={
            "status":            1,
            "gasUsed":           120_000,
            "gas":               200_000,
            "blockNumber":       19_100_000,
            "effectiveGasPrice": 22_000_000_000,
            "logs":              [],
        })
        receipt = mgr.wait_for_receipt("0xabc", timeout=10)
        assert isinstance(receipt, TxReceipt)
        assert receipt.status == 1

    def test_bump_tx_increases_fees(self):
        mgr = self._make_manager()
        original_tx = {
            "maxFeePerGas":         20_000_000_000,
            "maxPriorityFeePerGas": 2_000_000_000,
            "nonce":                5,
            "to":                   DUMMY_ADDRESS,
            "value":                0,
            "data":                 b"",
            "gas":                  200_000,
            "chainId":              1,
            "type":                 "0x2",
        }
        # sign_and_send returns a fake hash
        mgr.sign_and_send = MagicMock(return_value="0xbumped")
        result = mgr._bump_tx(original_tx)
        assert result == "0xbumped"
        # Verify the bumped tx passed to sign_and_send had higher fees
        bumped_tx = mgr.sign_and_send.call_args[0][0]
        assert bumped_tx["maxFeePerGas"] > original_tx["maxFeePerGas"]
        assert bumped_tx["maxPriorityFeePerGas"] > original_tx["maxPriorityFeePerGas"]

    def test_ensure_approval_skipped_when_sufficient(self):
        mgr = self._make_manager()

        # Mock ERC-20 contract
        mock_contract = MagicMock()
        mock_contract.functions.allowance.return_value.call.return_value = 2 ** 256 - 1
        mgr._w3.eth.contract = MagicMock(return_value=mock_contract)

        result = mgr.ensure_approval(DUMMY_ADDRESS, DUMMY_ADDRESS, 100)
        assert result is None   # no approval tx needed

    def test_sign_and_send_returns_hash(self):
        mgr = self._make_manager()
        fake_hash = bytes.fromhex("abcd" * 16)
        mgr._w3.eth.send_raw_transaction = MagicMock(return_value=fake_hash)
        # build a minimal signed tx mock
        mgr._account.sign_transaction = MagicMock()
        mgr._account.sign_transaction.return_value.raw_transaction = b"\x00" * 32

        tx = {
            "type": "0x2", "chainId": 1, "to": DUMMY_ADDRESS,
            "value": 0, "data": b"", "gas": 200_000, "nonce": 0,
            "maxFeePerGas": 25_000_000_000, "maxPriorityFeePerGas": 2_000_000_000,
        }
        h = mgr.sign_and_send(tx)
        assert isinstance(h, str)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigAlchemy:

    def test_get_rpc_url_from_rpc_url(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", "https://eth-mainnet.g.alchemy.com/v2/key1")
        monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
        # Re-instantiate config with patched env
        import importlib, config as cfg_mod
        importlib.reload(cfg_mod)
        assert cfg_mod.cfg.get_rpc_url() == "https://eth-mainnet.g.alchemy.com/v2/key1"

    def test_get_rpc_url_derived_from_api_key(self, monkeypatch):
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.delenv("ETH_RPC", raising=False)
        monkeypatch.setenv("ALCHEMY_API_KEY", "mykey123")
        import importlib, config as cfg_mod
        importlib.reload(cfg_mod)
        rpc = cfg_mod.cfg.get_rpc_url()
        assert rpc is not None
        assert "mykey123" in rpc
        assert rpc.startswith("https://eth-mainnet.g.alchemy.com/v2/")

    def test_get_rpc_url_none_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("RPC_URL",       raising=False)
        monkeypatch.delenv("ETH_RPC",       raising=False)
        monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
        import importlib, config as cfg_mod
        importlib.reload(cfg_mod)
        assert cfg_mod.cfg.get_rpc_url() is None

    def test_is_live_ready_with_alchemy_key(self, monkeypatch):
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.setenv("ALCHEMY_API_KEY", "key")
        monkeypatch.setenv("PRIVATE_KEY",     DUMMY_KEY)
        monkeypatch.setenv("WALLET_ADDRESS",  DUMMY_ADDRESS)
        import importlib, config as cfg_mod
        importlib.reload(cfg_mod)
        assert cfg_mod.cfg.is_live_ready() is True

    def test_is_live_ready_false_missing_key(self, monkeypatch):
        monkeypatch.setenv("RPC_URL", ALCHEMY_URL)
        monkeypatch.delenv("PRIVATE_KEY",    raising=False)
        monkeypatch.setenv("WALLET_ADDRESS", DUMMY_ADDRESS)
        import importlib, config as cfg_mod
        importlib.reload(cfg_mod)
        assert cfg_mod.cfg.is_live_ready() is False

    def test_validate_live_raises_with_missing_vars(self, monkeypatch):
        monkeypatch.delenv("RPC_URL",        raising=False)
        monkeypatch.delenv("ETH_RPC",        raising=False)
        monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
        monkeypatch.delenv("PRIVATE_KEY",    raising=False)
        monkeypatch.delenv("WALLET_ADDRESS", raising=False)
        import importlib, config as cfg_mod
        importlib.reload(cfg_mod)
        with pytest.raises(ValueError):
            cfg_mod.cfg.validate_live()


# ─────────────────────────────────────────────────────────────────────────────
# SwapExecutor construction (config path only — no live tx)
# ─────────────────────────────────────────────────────────────────────────────

class TestSwapExecutorConfig:

    def test_executor_raises_on_failed_connection(self):
        from vault.wallet_config import WalletConfig
        from engine.execution.swap_executor import SwapExecutor

        # Patch at module level (AlchemyClient is now a top-level import)
        with patch("engine.mainnet.alchemy_client.Web3") as MockWeb3:
            mock_w3 = MagicMock()
            mock_w3.is_connected.return_value = False
            MockWeb3.return_value = mock_w3
            wc = WalletConfig(DUMMY_KEY, ALCHEMY_URL)
            with pytest.raises(ConnectionError):
                SwapExecutor(wc, ALCHEMY_URL)

    def test_executor_created_with_mocked_client(self):
        from vault.wallet_config import WalletConfig
        from engine.execution import swap_executor as se_mod

        with patch.object(se_mod, "AlchemyClient") as MockAlchemy, \
             patch.object(se_mod, "TransactionManager"):
            mock_client = MagicMock()
            mock_client.w3.is_connected.return_value = True
            mock_client.w3.eth.contract.return_value = MagicMock()
            mock_client.chain_id = 1
            MockAlchemy.return_value = mock_client

            wc = WalletConfig(DUMMY_KEY, ALCHEMY_URL)
            executor = se_mod.SwapExecutor(wc, ALCHEMY_URL)
            assert executor is not None
            assert executor.wallet is wc

    def test_estimate_gas_usd_returns_float(self):
        from vault.wallet_config import WalletConfig
        from engine.execution import swap_executor as se_mod

        with patch.object(se_mod, "AlchemyClient") as MockAlchemy, \
             patch.object(se_mod, "TransactionManager"):
            mock_client = MagicMock()
            mock_client.w3.is_connected.return_value = True
            mock_client.w3.eth.contract.return_value = MagicMock()
            mock_client.chain_id = 1
            mock_client.get_eip1559_fees.return_value = (
                20_000_000_000, 2_000_000_000, 25_000_000_000
            )
            mock_client.w3.from_wei.return_value = 0.005
            MockAlchemy.return_value = mock_client

            wc = WalletConfig(DUMMY_KEY, ALCHEMY_URL)
            executor = se_mod.SwapExecutor(wc, ALCHEMY_URL)

            with patch("engine.market_data.MarketData") as MockMD:
                MockMD.return_value.get_price.return_value = 3000.0
                cost = executor.estimate_gas_usd()
            assert isinstance(cost, float)
            assert cost >= 0


# ── FlashLoanExecutor ──────────────────────────────────────────────────────────

class TestFlashLoanExecutor:
    """Offline tests for FlashLoanExecutor using main's TransactionManager API."""

    def _make_tx_manager(self):
        """Return a mocked TransactionManager (main's API)."""
        mgr = MagicMock()
        mgr.address = "0x" + "a" * 40
        mgr.build_tx.return_value = {"type": "0x2", "gas": 600_000}
        receipt = MagicMock()
        receipt.status = 1
        receipt.tx_hash = "0x" + "b" * 64
        receipt.block_number = 12345
        mgr.send_and_confirm.return_value = receipt
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = 1
        mock_w3.to_wei.return_value = int(1e18)
        mgr._w3 = mock_w3
        return mgr

    def _make_opportunity(self, profitable: bool = True):
        from nexus_arb.algorithms.bellman_ford import ArbitrageOpportunity, PoolPrice
        pool1 = PoolPrice(
            token_in="WETH", token_out="USDC",
            price=2000.0, price_after_fee=1994.0,
            fee_bps=30, liquidity=10.0, dex="uniswap_v3"
        )
        pool2 = PoolPrice(
            token_in="USDC", token_out="WETH",
            price=0.000510, price_after_fee=0.000509,
            fee_bps=30, liquidity=10.0, dex="sushiswap"
        )
        return ArbitrageOpportunity(
            cycle=["WETH", "USDC", "WETH"],
            pools=[pool1, pool2],
            gross_rate=1.03,
            net_rate=1.02 if profitable else 0.98,
            expected_profit_pct=2.0 if profitable else -2.0,
            max_input_eth=5.0,
            score=10.0 if profitable else 0.0,
        )

    def _make_executor(self):
        from nexus_arb.flash_loan_executor import FlashLoanExecutor
        tx_mgr = self._make_tx_manager()
        mock_w3 = tx_mgr._w3
        mock_contract = MagicMock()
        mock_contract.functions.paused.return_value.call.return_value = False
        mock_contract.functions.totalProfit.return_value.call.return_value = int(5e16)
        mock_contract.encodeABI.return_value = "0x" + "ab" * 32

        with patch("web3.Web3.to_checksum_address", side_effect=lambda x: x):
            exc = FlashLoanExecutor.__new__(FlashLoanExecutor)
            exc.w3               = mock_w3
            exc.contract_address = "0x" + "c" * 40
            exc.tx_manager       = tx_mgr
            exc.slippage         = 0.005
            exc.contract         = mock_contract
        return exc, tx_mgr

    def test_dry_run_returns_none(self):
        exc, _ = self._make_executor()
        opp = self._make_opportunity(profitable=True)
        result = exc.execute(opp, borrow_amount_eth=1.0, dry_run=True)
        assert result is None

    def test_paused_returns_none(self):
        exc, _ = self._make_executor()
        exc.contract.functions.paused.return_value.call.return_value = True
        opp = self._make_opportunity(profitable=True)
        result = exc.execute(opp, borrow_amount_eth=1.0, dry_run=False)
        assert result is None

    def test_execute_calls_build_tx_and_send_and_confirm(self):
        exc, tx_mgr = self._make_executor()
        opp = self._make_opportunity(profitable=True)
        exc.w3.to_wei.return_value = int(1e18)
        receipt = exc.execute(opp, borrow_amount_eth=1.0, dry_run=False)
        assert tx_mgr.build_tx.called
        assert tx_mgr.send_and_confirm.called
        assert receipt.status == 1

    def test_build_steps_count(self):
        exc, _ = self._make_executor()
        opp = self._make_opportunity()
        exc.w3.to_wei.return_value = int(1e18)
        steps = exc._build_steps(opp, borrow_wei=int(1e18))
        assert len(steps) == 2

    def test_build_steps_amount_in_zero(self):
        exc, _ = self._make_executor()
        opp = self._make_opportunity()
        steps = exc._build_steps(opp, borrow_wei=int(1e18))
        for s in steps:
            assert s.amount_in == 0  # use full contract balance

    def test_total_profit_eth(self):
        exc, _ = self._make_executor()
        profit = exc.total_profit_eth()
        assert profit == pytest.approx(0.05, abs=0.01)  # 5e16 wei = 0.05 ETH

    def test_invalid_cycle_raises(self):
        from nexus_arb.flash_loan_executor import FlashLoanExecutor
        from nexus_arb.algorithms.bellman_ford import ArbitrageOpportunity, PoolPrice
        exc, _ = self._make_executor()
        pool = PoolPrice("A", "B", 1.0, 1.0, 30, 1.0, "uniswap_v3")
        opp = ArbitrageOpportunity(
            cycle=["A", "B"],  # too short
            pools=[pool],
            gross_rate=1.01, net_rate=1.01, expected_profit_pct=1.0,
            max_input_eth=1.0, score=1.0,
        )
        with pytest.raises(ValueError, match="cycle of length"):
            exc.execute(opp, borrow_amount_eth=1.0, dry_run=False)

    def test_swap_step_to_tuple(self):
        from nexus_arb.flash_loan_executor import SwapStep, DEX_UNISWAP_V3
        step = SwapStep(
            dex_type=DEX_UNISWAP_V3,
            token_in="0x" + "a" * 40,
            token_out="0x" + "b" * 40,
            amount_in=0,
            min_amount_out=995_000_000,
            extra_data=b"\x00\x01\xf4",
        )
        t = step.to_tuple()
        assert len(t) == 6
        assert t[0] == DEX_UNISWAP_V3
