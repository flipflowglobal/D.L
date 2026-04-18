"""
engine/execution/executor.py — Simulated (paper-trading) trade executor.

Delegates buy/sell to Portfolio so it stays decoupled from live blockchain
calls during back-testing and simulation.  Used by trade.py (paper mode)
and autonomy.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vault.wallet_config import WalletConfig
    from engine.portfolio import Portfolio

logger = logging.getLogger("aureon.executor")


class Executor:
    """
    Simulated trade executor.

    Delegates buy/sell logic to the Portfolio object so it stays
    decoupled from live blockchain calls during back-testing.
    """

    def __init__(
        self,
        wallet: WalletConfig | None = None,
        rpc_url: str | None = None,
    ) -> None:
        self.wallet  = wallet
        self.rpc_url = rpc_url

    def execute_buy(
        self, portfolio: Portfolio, price: float, amount: float
    ) -> bool:
        """Execute a simulated BUY and log the result."""
        success = portfolio.buy(price, amount)
        if success:
            logger.info("BUY  %.4f ETH @ $%,.2f", amount, price)
        else:
            logger.warning("BUY  FAILED — insufficient USD balance")
        return success

    def execute_sell(
        self, portfolio: Portfolio, price: float, amount: float
    ) -> bool:
        """Execute a simulated SELL and log the result."""
        success = portfolio.sell(price, amount)
        if success:
            logger.info("SELL %.4f ETH @ $%,.2f", amount, price)
        else:
            logger.warning("SELL FAILED — insufficient ETH balance")
        return success
