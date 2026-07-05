from pydantic import BaseModel


class BotCapabilities(BaseModel):
    chart: bool = True
    indicators: bool = True
    backtest: bool = True
    live_trade: bool = False
    account: bool = False
    orders: bool = False

    @classmethod
    def research(cls) -> "BotCapabilities":
        return cls()

    @classmethod
    def trading(cls) -> "BotCapabilities":
        return cls(live_trade=True, account=True, orders=True)
