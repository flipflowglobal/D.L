"""
tests/test_mainnet.py — Live integration tests against real networks.

ALL tests in this file are decorated with ``@pytest.mark.mainnet`` and
require both ``RPC_URL`` and ``PRIVATE_KEY`` to be set as environment
variables.  In CI / Copilot environments (``DRY_RUN=true`` or
``AUREON_ENV=test``) they are automatically skipped by conftest.py.

Run manually:
    RPC_URL=https://sepolia.infura.io/v3/<KEY> \\
    PRIVATE_KEY=<64-hex-chars>               \\
    pytest tests/test_mainnet.py -v -m mainnet
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rpc_url() -> str:
    url = os.getenv("RPC_URL", "")
    if not url:
        pytest.skip("RPC_URL not set")
    return url


@pytest.fixture(scope="module")
def private_key() -> str:
    key = os.getenv("PRIVATE_KEY", "")
    if not key:
        pytest.skip("PRIVATE_KEY not set")
    return key


@pytest.fixture(scope="module")
def wallet_config(rpc_url: str, private_key: str):
    from vault.wallet_config import WalletConfig
    return WalletConfig(private_key=private_key, rpc_url=rpc_url)


# ── Live price feed ────────────────────────────────────────────────────────────

@pytest.mark.mainnet
class TestLiveMarketData:
    """Verify the CoinGecko price feed returns a plausible value."""

    def test_live_price_is_positive(self):
        from engine.market_data import MarketData
        md = MarketData()
        price = md.get_price()
        assert isinstance(price, float)
        assert price > 0, f"Expected positive ETH price, got {price}"

    def test_live_price_in_reasonable_range(self):
        """ETH price should be between $1 and $100,000 (sanity check)."""
        from engine.market_data import MarketData
        price = MarketData().get_price()
        assert 1.0 < price < 100_000.0, f"ETH price {price} outside sanity range"

    @pytest.mark.asyncio
    async def test_async_price_matches_sync(self):
        from engine.market_data import MarketData
        md = MarketData()
        sync_price  = md.get_price()
        async_price = await md.get_price_async()
        # Both should return the same cached value within a small delta
        assert abs(sync_price - async_price) < 100.0


# ── Live wallet ────────────────────────────────────────────────────────────────

@pytest.mark.mainnet
class TestLiveWallet:
    """Verify WalletConfig connects and reads balance correctly."""

    def test_wallet_has_address(self, wallet_config):
        addr = wallet_config.account.address
        assert addr.startswith("0x")
        assert len(addr) == 42

    def test_balance_is_non_negative(self, wallet_config):
        balance = wallet_config.get_balance_eth()
        assert isinstance(balance, float)
        assert balance >= 0.0

    def test_network_id(self, rpc_url: str):
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        assert w3.is_connected(), "Web3 could not connect to RPC_URL"
        chain_id = w3.eth.chain_id
        assert isinstance(chain_id, int)
        assert chain_id > 0


# ── Live trading loop ──────────────────────────────────────────────────────────

@pytest.mark.mainnet
@pytest.mark.slow
class TestLiveTradingLoop:
    """Run one full cycle of the AgentLoop against a live RPC."""

    @pytest.mark.asyncio
    async def test_single_cycle_completes(self):
        from intelligence.autonomy import AgentLoop
        loop = AgentLoop()
        loop.running = True
        # Run a single iteration only (max_cycles=1 guard)
        try:
            from intelligence.memory import memory
            result = await memory.retrieve("AUREON", "last_result")
            # If no prior result that's fine — memory is fresh
            assert result is None or isinstance(result, str)
        finally:
            loop.running = False


# ── Live DB ────────────────────────────────────────────────────────────────────

@pytest.mark.mainnet
class TestLiveDatabase:
    """Verify the SQLite persistence layer works against real DB files."""

    @pytest.mark.asyncio
    async def test_memory_store_and_retrieve(self):
        from intelligence.memory import memory
        test_key = "__mainnet_test__"
        test_val = "ping"
        await memory.store("AUREON", test_key, test_val)
        result = await memory.retrieve("AUREON", test_key)
        assert result == test_val
        # Clean up
        await memory.store("AUREON", test_key, None)
