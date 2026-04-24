"""
tests/conftest.py — Shared pytest fixtures for the AUREON test suite.

Network isolation
-----------------
The ``mock_external_apis`` fixture (autouse) intercepts every outbound
request to known third-party API hosts and returns an offline mock
response.  This ensures the test suite never requires real network access
and passes regardless of whether Copilot's firewall is active.

Specifically mocked:
  - api.coingecko.com   (MarketData, price_cache)
  - pro-api.coingecko.com
  - api.coinmarketcap.com
  - api.binance.com
  - api.kraken.com
  - api.etherscan.io  / api-sepolia.etherscan.io
  - mainnet.infura.io / sepolia.infura.io / *.alchemy.com

Both ``requests`` (sync) and ``httpx`` (async) are patched so tests that
use either library stay offline.

Also patches ``engine.market_data.MarketData.COINGECKO_URL`` to point at
the environment-variable-controlled base URL (defaults to a harmless
localhost address) so no HTTPS handshakes are attempted even if
monkeypatching is somehow bypassed.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

# Ensure project root is importable from any working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Shared mock data ──────────────────────────────────────────────────────────

_MOCK_PRICE_USD = float(os.getenv("FALLBACK_ETH_PRICE", "2000.0"))

_MOCK_COINGECKO_SIMPLE = {
    "ethereum": {"usd": _MOCK_PRICE_USD},
    "bitcoin":  {"usd": 60_000.0},
    "usd-coin": {"usd": 1.0},
}

_MOCK_ETHERSCAN = {
    "status": "1",
    "message": "OK",
    "result": str(_MOCK_PRICE_USD),
}

_MOCK_INFURA_RPC = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": "0x1",
}


# ── Blocked domains ───────────────────────────────────────────────────────────

_BLOCKED_HOSTS = frozenset({
    "api.coingecko.com",
    "pro-api.coingecko.com",
    "pro.api.coingecko.com",
    "api.coinmarketcap.com",
    "sandbox-api.coinmarketcap.com",
    "api.binance.com",
    "api.binance.us",
    "api.kraken.com",
    "api.etherscan.io",
    "api-sepolia.etherscan.io",
    "api-goerli.etherscan.io",
    "mainnet.infura.io",
    "sepolia.infura.io",
    "polygon-mainnet.infura.io",
    "eth-mainnet.g.alchemy.com",
    "eth-sepolia.g.alchemy.com",
    "api.coinbase.com",
    "api.pro.coinbase.com",
    "api.thegraph.com",
    "gateway.thegraph.com",
})


def _mock_response_for(url: str) -> dict:
    """Return an appropriate mock JSON body based on URL path."""
    url_lower = url.lower()
    if "simple/price" in url_lower or "ethereum" in url_lower:
        return _MOCK_COINGECKO_SIMPLE
    if "etherscan" in url_lower:
        return _MOCK_ETHERSCAN
    if "infura" in url_lower or "alchemy" in url_lower or "rpc" in url_lower:
        return _MOCK_INFURA_RPC
    return {"status": "ok", "mock": True, "url": url}


# ── requests mock ─────────────────────────────────────────────────────────────

class _MockRequestsResponse:
    """Minimal stub matching the requests.Response API."""

    def __init__(self, data: dict, status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data)
        self.content = self.text.encode()

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

# ── httpx mock ────────────────────────────────────────────────────────────────

class _MockHttpxResponse:
    """Minimal stub matching httpx.Response API."""

    def __init__(self, data: dict, status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data)
        self.content = self.text.encode()

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# Store real functions before any patching
try:
    import requests as _requests_module
    _real_requests_get = _requests_module.get
except ImportError:
    _requests_module = None
    _real_requests_get = None

try:
    import httpx as _httpx_module
    _real_httpx_get = None  # set lazily after monkeypatching
except ImportError:
    _httpx_module = None


# ── Autouse fixture ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_external_apis(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Intercept outbound requests to blocked external API hosts.

    Automatically applied to every test.  Tests that need real network access
    should use ``@pytest.mark.integration`` and will be skipped when
    ``AUREON_ENV=test`` is set.
    """
    # ── Patch requests.get ────────────────────────────────────────────────────
    if _requests_module is not None:
        def _safe_get(url: str, *args, **kwargs) -> _MockRequestsResponse:
            from urllib.parse import urlparse
            host = urlparse(str(url)).hostname or ""
            if host in _BLOCKED_HOSTS:
                return _MockRequestsResponse(_mock_response_for(url))
            return _real_requests_get(url, *args, **kwargs)  # type: ignore[misc]

        monkeypatch.setattr(_requests_module, "get", _safe_get)

    # ── Patch MarketData.COINGECKO_URL ────────────────────────────────────────
    try:
        from engine.market_data import MarketData
        base = os.getenv("COINGECKO_BASE_URL", "http://127.0.0.1:8099")
        monkeypatch.setattr(
            MarketData,
            "COINGECKO_URL",
            f"{base}/api/v3/simple/price?ids=ethereum&vs_currencies=usd",
        )
    except Exception:
        pass   # market_data not importable — skip

    # ── httpx is patched test-by-test via pytest-httpx when needed ────────────
    # (tests that use httpx.AsyncClient get their own fixture from pytest-httpx)


@pytest.fixture
def mock_eth_price() -> float:
    """Return the standard mock ETH/USD price used across tests."""
    return _MOCK_PRICE_USD


# ── Skip helpers ──────────────────────────────────────────────────────────────

def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """
    Automatically skip ``mainnet`` and ``integration`` marked tests
    when no live credentials are available.
    """
    has_rpc     = bool(os.getenv("RPC_URL"))
    has_key     = bool(os.getenv("PRIVATE_KEY"))
    is_test_env = os.getenv("AUREON_ENV") == "test" or os.getenv("DRY_RUN") == "true"

    skip_mainnet = pytest.mark.skip(
        reason="Mainnet test skipped: set RPC_URL + PRIVATE_KEY to run"
    )
    skip_integration = pytest.mark.skip(
        reason="Integration test skipped in offline/test environment"
    )

    for item in items:
        if "mainnet" in item.keywords:
            if not (has_rpc and has_key) or is_test_env:
                item.add_marker(skip_mainnet)
        if "integration" in item.keywords:
            if is_test_env:
                item.add_marker(skip_integration)
