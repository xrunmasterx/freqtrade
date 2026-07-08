from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from freqtrade.markets import CachedAShareCalendar


_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MARKET_CLOSE = time(15, 0)


def candle_time_for_trading_date(value: Any) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        trading_date = timestamp.tz_convert(_ASIA_SHANGHAI).date()
    else:
        trading_date = timestamp.date()
    return str(pd.Timestamp(trading_date, tz="UTC"))


def effective_candle_time_for_publish_time(
    value: Any,
    calendar: CachedAShareCalendar | None,
) -> str:
    if calendar is None:
        raise ValueError(
            "A-share effective candle alignment requires a trading calendar; "
            "calendar is required."
        )

    timestamp = pd.to_datetime(value, utc=True).tz_convert(_ASIA_SHANGHAI)
    publish_date = timestamp.date()

    if calendar.is_trading_day(publish_date) and timestamp.time() <= _MARKET_CLOSE:
        effective_date = publish_date
    else:
        effective_date = calendar.next_trading_day(publish_date)

    return str(pd.Timestamp(effective_date, tz="UTC"))


def is_available_at(publish_time: object | None, decision_time: object) -> bool:
    if publish_time is None or publish_time == "":
        return True
    return pd.to_datetime(publish_time, utc=True) <= pd.to_datetime(decision_time, utc=True)
