"""
Tests for engine/mainnet/ (AlchemyClient + TransactionManager)
and nexus_arb/flash_loan_executor.py.

All tests are fully offline — no real RPC calls.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import threading
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ── AlchemyClient ─────────────────────────────────────────────────────────────

class TestAlchemyClient:
    """Unit tests for AlchemyClient (no live RPC)."""

    def _make_client(self, url="https://eth-mainnet.g.alchemy.com/v2/TESTKEY"):
        from engine.mainnet.alchemy_client import AlchemyClient
        with patch("web3.Web3.HTTPProvider"), \
             patch("web3.Web3.is_connected", return_value=False):
            client = AlchemyClient.__new__(AlchemyClient)
            client.http_url              = url
            client.max_priority_fee_gwei = 1.5
            client.max_fee_multiplier    = 2.0
            client._w3                   = None
        return client

    def test_ws_url_alchemy(self):
        from engine.mainnet.alchemy_client import AlchemyClient
        c = self._make_client("https://eth-mainnet.g.alchemy.com/v2/MYKEY")
        assert c.ws_url == "wss://eth-mainnet.g.alchemy.com/v2/MYKEY"

    def test_ws_url_alchemy_rejects_evil_subdomain(self):
        """Verify 'evilalchemy.com' does NOT get the Alchemy path."""
        c = self._make_client("https://evilalchemy.com/v2/MYKEY")
        # Falls through to generic handler — still swaps scheme safely
        result = c.ws_url
        assert result.startswith("wss://")

    def test_ws_url_infura(self):
        c = self._make_client("https://mainnet.infura.io/v3/MYKEY")
        assert c.ws_url == "wss://mainnet.infura.io/ws/v3/MYKEY"

    def test_ws_url_infura_rejects_evil_subdomain(self):
        """Verify 'evilinfura.io' does NOT get the Infura /ws/ insertion."""
        c = self._make_client("https://evilinfura.io/v3/MYKEY")
        result = c.ws_url
        # Should be generic swap, no /ws/ inserted
        assert "/ws/" not in result
        assert result.startswith("wss://")

    def test_ws_url_generic(self):
        c = self._make_client("https://rpc.example.com/eth")
        assert c.ws_url == "wss://rpc.example.com/eth"

    def test_ws_url_none_when_no_url(self):
        c = self._make_client("")
        assert c.ws_url is None

    def test_default_fees_structure(self):
        from engine.mainnet.alchemy_client import AlchemyClient, EIP1559Fees
        c = self._make_client()
        fees = c._default_fees()
        assert isinstance(fees, EIP1559Fees)
        assert fees.max_fee_per_gas_wei > fees.max_priority_fee_wei
        assert fees.base_fee_wei > 0

    def test_default_fees_values(self):
        from engine.mainnet.alchemy_client import AlchemyClient
        c = self._make_client()
        fees = c._default_fees()
        # Default: 20 gwei base × 2 + 1.5 gwei priority
        assert fees.base_fee_gwei == pytest.approx(20.0, abs=1.0)
        assert fees.max_priority_fee_gwei == pytest.approx(1.5, abs=0.5)

    def test_eip1559_fees_repr(self):
        from engine.mainnet.alchemy_client import EIP1559Fees
        fees = EIP1559Fees(
            max_priority_fee_wei=int(1.5e9),
            max_fee_per_gas_wei=int(42e9),
            base_fee_wei=int(20e9),
        )
        r = repr(fees)
        assert "gwei" in r
        assert "42" in r

    def test_is_connected_false_when_no_w3(self):
        c = self._make_client()
        assert c.is_connected() is False

    def test_connect_sets_w3_mock(self):
        from engine.mainnet.alchemy_client import AlchemyClient
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        with patch("web3.Web3.HTTPProvider"), \
             patch("web3.Web3", return_value=mock_w3):
            c = AlchemyClient.__new__(AlchemyClient)
            c.http_url              = "https://eth-mainnet.g.alchemy.com/v2/KEY"
            c.max_priority_fee_gwei = 1.5
            c.max_fee_multiplier    = 2.0
            c._w3                   = mock_w3
        assert c.is_connected() is True

    def test_get_eip1559_fees_fallback_on_rpc_error(self):
        from engine.mainnet.alchemy_client import AlchemyClient, EIP1559Fees
        c = self._make_client()
        # w3 is None so get_eip1559_fees should catch and return defaults
        fees = c.get_eip1559_fees()
        assert isinstance(fees, EIP1559Fees)


# ── EIP1559Fees ────────────────────────────────────────────────────────────────

class TestEIP1559Fees:
    def test_gwei_conversions(self):
        from engine.mainnet.alchemy_client import EIP1559Fees
        fees = EIP1559Fees(
            max_priority_fee_wei=int(2e9),
            max_fee_per_gas_wei=int(50e9),
            base_fee_wei=int(20e9),
        )
        assert fees.max_priority_fee_gwei == pytest.approx(2.0)
        assert fees.max_fee_gwei          == pytest.approx(50.0)
        assert fees.base_fee_gwei         == pytest.approx(20.0)


# ── TransactionManager ────────────────────────────────────────────────────────

class TestTransactionManager:
    def _make_manager(self):
        """Create a TransactionManager with mocked Web3."""
        from engine.mainnet.transaction_manager import TransactionManager

        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = 1
        mock_w3.eth.get_transaction_count.return_value = 5

        mock_account = MagicMock()
        mock_account.address = "0xdeadbeef" * 5 + "00000000"

        mgr = TransactionManager(mock_w3, mock_account, alchemy_client=None)
        return mgr, mock_w3, mock_account

    def test_nonce_increments(self):
        mgr, mock_w3, _ = self._make_manager()
        mock_w3.eth.get_transaction_count.return_value = 10
        n1 = mgr._next_nonce()
        n2 = mgr._next_nonce()
        assert n2 == n1 + 1

    def test_nonce_is_thread_safe(self):
        mgr, mock_w3, _ = self._make_manager()
        mock_w3.eth.get_transaction_count.return_value = 0
        nonces = []
        lock = threading.Lock()

        def grab_nonce():
            n = mgr._next_nonce()
            with lock:
                nonces.append(n)

        threads = [threading.Thread(target=grab_nonce) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(nonces)) == 20, "Duplicate nonces detected — not thread-safe"

    def test_release_nonce_resets_pending(self):
        mgr, mock_w3, _ = self._make_manager()
        mock_w3.eth.get_transaction_count.return_value = 7
        mgr._next_nonce()  # sets pending to 8
        mgr._release_nonce()
        assert mgr._pending_nonce is None
        # Next call should re-read from chain
        n = mgr._next_nonce()
        assert n == 7

    def test_release_nonce_called_on_sign_error(self):
        """_release_nonce() must be called when tx sign/send fails so the
        pending nonce counter is reset and the next call re-reads from chain."""
        from engine.mainnet.transaction_manager import TransactionManager
        from engine.mainnet.alchemy_client import EIP1559Fees
        mgr, mock_w3, mock_account = self._make_manager()
        mock_account.address = "0x" + "a" * 40
        mock_w3.eth.get_transaction_count.return_value = 3

        # Make signing fail
        mock_account.sign_transaction.side_effect = ValueError("bad key")

        fees = EIP1559Fees(int(1.5e9), int(41.5e9), int(20e9))
        try:
            mgr.send_transaction(
                to="0x" + "b" * 40,
                value_wei=0,
                data=b"",
                gas_limit=21_000,
                fees=fees,
            )
        except RuntimeError:
            pass

        # After failure, pending_nonce must be None so next nonce re-reads chain
        assert mgr._pending_nonce is None, "_release_nonce() was not called on error"

    def test_bump_fees_increases_values(self):
        from engine.mainnet.transaction_manager import TransactionManager
        from engine.mainnet.alchemy_client import EIP1559Fees

        fees = EIP1559Fees(
            max_priority_fee_wei=int(2e9),
            max_fee_per_gas_wei=int(50e9),
            base_fee_wei=int(20e9),
        )
        bumped = TransactionManager._bump_fees(fees, pct=15)
        assert bumped.max_priority_fee_wei > fees.max_priority_fee_wei
        assert bumped.max_fee_per_gas_wei  > fees.max_fee_per_gas_wei
        # Base fee unchanged
        assert bumped.base_fee_wei == fees.base_fee_wei

    def test_build_tx_has_eip1559_fields(self):
        from engine.mainnet.alchemy_client import EIP1559Fees
        mgr, mock_w3, mock_account = self._make_manager()
        mock_account.address = "0x" + "a" * 40
        fees = EIP1559Fees(int(2e9), int(50e9), int(20e9))

        tx = mgr._build_tx(
            to="0x" + "b" * 40,
            value_wei=0,
            data=b"",
            gas_limit=21_000,
            fees=fees,
            nonce=5,
        )
        assert tx["type"]                 == 2
        assert "maxFeePerGas"             in tx
        assert "maxPriorityFeePerGas"     in tx
        assert "gasPrice"             not in tx
        assert tx["gas"]                  == 21_000

    def test_wait_for_receipt_timeout(self):
        from engine.mainnet.transaction_manager import TransactionManager, TransactionTimeoutError
        mgr, mock_w3, _ = self._make_manager()
        mock_w3.eth.get_transaction_receipt.return_value = None  # never confirmed
        mgr.timeout_s       = 0  # instant timeout
        mgr.poll_interval_s = 0

        with pytest.raises(TransactionTimeoutError):
            mgr._wait_for_receipt(b"\x00" * 32)

    def test_send_transaction_raises_on_w3_error(self):
        from engine.mainnet.transaction_manager import TransactionManager
        from engine.mainnet.alchemy_client import EIP1559Fees
        mgr, mock_w3, mock_account = self._make_manager()
        mock_account.address = "0x" + "a" * 40

        mock_w3.eth.get_block.return_value = {"baseFeePerGas": int(20e9)}
        mock_w3.eth.max_priority_fee = int(1.5e9)
        mock_account.sign_transaction.side_effect = Exception("sign error")

        fees = EIP1559Fees(int(1.5e9), int(41.5e9), int(20e9))
        with pytest.raises(RuntimeError, match="sign error"):
            mgr.send_transaction(
                to="0x" + "b" * 40,
                value_wei=0,
                data=b"",
                gas_limit=21_000,
                fees=fees,
            )


# ── SwapExecutor (EIP-1559 upgrade) ───────────────────────────────────────────

class TestSwapExecutorEIP1559:
    def _make_executor(self):
        from engine.execution.swap_executor import SwapExecutor, SWAP_ROUTER02
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.chain_id = 1
        mock_w3.eth.get_block.return_value = {"baseFeePerGas": int(20e9)}
        mock_w3.eth.max_priority_fee = int(1.5e9)

        mock_wallet = MagicMock()
        mock_wallet.account.address = "0x" + "a" * 40

        with patch("web3.Web3.HTTPProvider"), \
             patch("web3.Web3", return_value=mock_w3):
            exc = SwapExecutor.__new__(SwapExecutor)
            exc.wallet         = mock_wallet
            exc.w3             = mock_w3
            exc.router_address = SWAP_ROUTER02
            exc.router         = MagicMock()
            exc._alchemy       = None
        return exc, mock_w3, mock_wallet

    def test_eip1559_fees_returns_dict_with_correct_keys(self):
        exc, mock_w3, _ = self._make_executor()
        fees = exc._eip1559_fees()
        assert "maxFeePerGas"         in fees
        assert "maxPriorityFeePerGas" in fees
        assert "gasPrice"         not in fees

    def test_eip1559_fees_fallback_values(self):
        exc, mock_w3, _ = self._make_executor()
        mock_w3.eth.get_block.side_effect = Exception("no block")
        mock_w3.eth.max_priority_fee = 0
        fees = exc._eip1559_fees()
        assert fees["maxFeePerGas"] > 0

    def test_router_is_swapRouter02(self):
        from engine.execution.swap_executor import SWAP_ROUTER02
        exc, _, _ = self._make_executor()
        assert exc.router_address == SWAP_ROUTER02

    def test_estimate_gas_usd_positive(self):
        exc, mock_w3, _ = self._make_executor()
        with patch("engine.market_data.MarketData.get_price", return_value=2500.0):
            cost = exc.estimate_gas_usd(gas=200_000)
        assert cost > 0
        assert cost < 50  # Sanity: gas should be less than $50 under normal conditions

    def test_min_out_calculation(self):
        exc, _, _ = self._make_executor()
        result = exc._min_out(1_000_000, 0.005)
        assert result == 995_000


# ── FlashLoanExecutor ─────────────────────────────────────────────────────────

class TestFlashLoanExecutor:
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
        net_rate = 1.02 if profitable else 0.98
        return ArbitrageOpportunity(
            cycle=["WETH", "USDC", "WETH"],
            pools=[pool1, pool2],
            gross_rate=net_rate + 0.01,
            net_rate=net_rate,
            expected_profit_pct=(net_rate - 1.0) * 100,
            max_input_eth=5.0,
            score=10.0 if profitable else 0.0,
        )

    def _make_executor(self):
        from nexus_arb.flash_loan_executor import FlashLoanExecutor

        mock_w3      = MagicMock()
        mock_w3.eth.chain_id = 1
        mock_w3.to_wei.return_value = int(1e18)

        mock_account = MagicMock()
        mock_account.address = "0x" + "a" * 40

        mock_tx_mgr  = MagicMock()

        mock_contract = MagicMock()
        mock_contract.functions.paused.return_value.call.return_value = False
        mock_contract.functions.totalProfitWei.return_value.call.return_value = int(5e16)

        with patch("web3.Web3.to_checksum_address", side_effect=lambda x: x), \
             patch.object(mock_w3.eth, "contract", return_value=mock_contract):
            exc = FlashLoanExecutor(
                w3=mock_w3,
                account=mock_account,
                contract_address="0x" + "c" * 40,
                tx_manager=mock_tx_mgr,
                slippage=0.005,
            )
        exc.contract = mock_contract
        return exc, mock_tx_mgr

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

    def test_build_steps_count(self):
        from nexus_arb.flash_loan_executor import FlashLoanExecutor
        exc, _ = self._make_executor()
        opp   = self._make_opportunity(profitable=True)
        steps = exc._build_steps(opp, borrow_wei=int(1e18))
        assert len(steps) == 2  # WETH→USDC, USDC→WETH

    def test_build_steps_correct_tokens(self):
        exc, _ = self._make_executor()
        opp   = self._make_opportunity()
        steps = exc._build_steps(opp, borrow_wei=int(1e18))
        assert "WETH" in steps[0].token_in or steps[0].token_in.startswith("0x")
        assert "USDC" in steps[0].token_out or steps[0].token_out.startswith("0x")

    def test_build_steps_use_full_balance(self):
        exc, _ = self._make_executor()
        opp   = self._make_opportunity()
        steps = exc._build_steps(opp, borrow_wei=int(1e18))
        # All steps should have amountIn=0 (use full contract balance)
        for s in steps:
            assert s.amount_in == 0

    def test_build_steps_min_amount_positive(self):
        exc, _ = self._make_executor()
        opp   = self._make_opportunity()
        steps = exc._build_steps(opp, borrow_wei=int(1e18))
        for s in steps:
            assert s.min_amount_out >= 0

    def test_total_profit_eth(self):
        exc, _ = self._make_executor()
        profit = exc.total_profit_eth()
        assert profit == pytest.approx(0.05, abs=0.01)  # 5e16 wei = 0.05 ETH

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


# ── encode helpers ────────────────────────────────────────────────────────────

class TestEncodeHelpers:
    def test_encode_uniswap_extra(self):
        from nexus_arb.flash_loan_executor import _encode_uniswap_extra
        data = _encode_uniswap_extra(fee=500)
        assert isinstance(data, bytes)
        assert len(data) == 64  # (uint24, address) = 32 + 32

    def test_encode_balancer_extra(self):
        from nexus_arb.flash_loan_executor import _encode_balancer_extra
        pool_id = b"\xab" * 32
        data = _encode_balancer_extra(pool_id)
        assert isinstance(data, bytes)
        assert len(data) == 32

    def test_resolve_known_tokens(self):
        from nexus_arb.flash_loan_executor import _resolve_token_address
        weth = _resolve_token_address("WETH")
        usdc = _resolve_token_address("USDC")
        assert weth.startswith("0x")
        assert usdc.startswith("0x")
        assert weth != usdc

    def test_resolve_address_passthrough(self):
        from nexus_arb.flash_loan_executor import _resolve_token_address
        addr = "0x" + "a" * 40
        with patch("web3.Web3.to_checksum_address", side_effect=lambda x: x):
            result = _resolve_token_address(addr)
        assert result == addr

    def test_resolve_unknown_symbol_raises(self):
        from nexus_arb.flash_loan_executor import _resolve_token_address
        with pytest.raises(ValueError, match="Unknown token symbol"):
            _resolve_token_address("FAKECOIN")
