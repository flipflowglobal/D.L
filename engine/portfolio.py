class Portfolio:
    """
    Simulated portfolio tracking USD and ETH balances.
    buy() / sell() update balances and return success/failure.
    """

    def __init__(self, initial_usd: float = 10_000.0):
        self.balance_usd = initial_usd
        self.balance_eth = 0.0

    def buy(self, price: float, amount: float) -> bool:
        cost = price * amount
        if self.balance_usd < cost:
            return False
        self.balance_usd -= cost
        self.balance_eth += amount
        return True

    def sell(self, price: float, amount: float) -> bool:
        if self.balance_eth < amount:
            return False
        self.balance_eth -= amount
        self.balance_usd += price * amount
        return True

    def summary(self) -> dict:
        return {
            "balance_usd": round(self.balance_usd, 2),
            "balance_eth": round(self.balance_eth, 6),
        }
