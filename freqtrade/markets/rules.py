from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from freqtrade.markets.calendar_store import CachedAShareCalendar
from freqtrade.markets.status_store import AShareDailyStatus


_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")


class AShareMarketRules:
    lot_size = 100
    commission_rate = 0.0003
    stamp_tax_rate = 0.001

    def whole_lot_shares(self, cash: float, price: float) -> int:
        lot_cost = price * self.lot_size * (1 + self.commission_rate)
        return int(cash // lot_cost) * self.lot_size

    def entry_fee(self, trade_value: float) -> float:
        return trade_value * self.commission_rate

    def exit_fee(self, trade_value: float) -> tuple[float, float]:
        return trade_value * self.commission_rate, trade_value * self.stamp_tax_rate

    def can_fill_order(
        self,
        side: str,
        price: float,
        status: AShareDailyStatus | None,
    ) -> bool:
        if side not in {"buy", "sell"}:
            raise ValueError(f"Unsupported A-share order side: {side}")
        if status is None:
            return True
        if status.suspended:
            return False
        if status.volume is not None and status.volume <= 0:
            return False
        if status.listed_date is not None and status.date < status.listed_date:
            return False
        if status.delisted_date is not None and status.date > status.delisted_date:
            return False
        if side == "buy" and status.limit_up is not None and price >= status.limit_up:
            return False
        if side == "sell" and status.limit_down is not None and price <= status.limit_down:
            return False
        return True

    def can_sell(
        self,
        entry_date: Any,
        execution_date: Any,
        calendar: CachedAShareCalendar | None = None,
    ) -> bool:
        if calendar is not None:
            return calendar.next_trading_day(entry_date) <= self._trading_date(execution_date)
        return self._trading_date(execution_date) > self._trading_date(entry_date)

    def _trading_date(self, value: Any) -> Any:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.date()
        return timestamp.tz_convert(_ASIA_SHANGHAI).date()
