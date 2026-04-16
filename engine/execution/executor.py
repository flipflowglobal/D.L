from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from vault.wallet_config import WalletConfig
    from engine.portfolio import Portfolio


class Executor:
    """
    Simulated trade executor.
    Delegates buy/sell logic to the Portfolio object so it stays
    decoupled from live blockchain calls during back-testing.
    """

    def __init__(self, wallet: Optional["WalletConfig"] = None, rpc_url: Optional[str] = None):
        self.wallet = wallet
        self.rpc_url = rpc_url

    def execute_buy(self, portfolio: "Portfolio", price: float, amount: float) -> bool:
        success = portfolio.buy(price, amount)
        if success:
            print(f"[Executor] BUY  {amount} ETH @ ${price:,.2f}")
        else:
            print("[Executor] BUY  FAILED — insufficient USD balance")
        return success

    def execute_sell(self, portfolio: "Portfolio", price: float, amount: float) -> bool:
        success = portfolio.sell(price, amount)
        if success:
            print(f"[Executor] SELL {amount} ETH @ ${price:,.2f}")
        else:
            print("[Executor] SELL FAILED — insufficient ETH balance")
        return success
