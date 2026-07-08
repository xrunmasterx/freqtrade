from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from freqtrade.markets.calendar_store import CachedAShareCalendar


_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MORNING_START = time(9, 30)
_MORNING_END = time(11, 30)
_AFTERNOON_START = time(13, 0)
_AFTERNOON_END = time(15, 0)


class AShareCalendar:
    def __init__(
        self,
        closed_dates: set[str] | None = None,
        cached_calendar: CachedAShareCalendar | None = None,
    ) -> None:
        self.closed_dates = closed_dates or set()
        self.cached_calendar = cached_calendar

    def is_trading_day(self, value: date | datetime | str) -> bool:
        if self.cached_calendar is not None:
            return self.cached_calendar.is_trading_day(value)

        trading_date = _to_shanghai_date(value)
        return trading_date.weekday() < 5 and trading_date.isoformat() not in self.closed_dates

    def next_trading_day(self, value: date | datetime | str) -> date:
        if self.cached_calendar is not None:
            return self.cached_calendar.next_trading_day(value)

        candidate = _to_shanghai_date(value)
        while True:
            candidate = candidate + timedelta(days=1)
            if self.is_trading_day(candidate):
                return candidate

    def is_session_open(self, dt: datetime) -> bool:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_ASIA_SHANGHAI)

        local_dt = dt.astimezone(_ASIA_SHANGHAI)

        if not self.is_trading_day(local_dt):
            return False

        local_time = local_dt.time()
        return (
            _MORNING_START <= local_time < _MORNING_END
            or _AFTERNOON_START <= local_time < _AFTERNOON_END
        )


def _to_shanghai_date(value: date | datetime | str) -> date:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(_ASIA_SHANGHAI)
    return timestamp.date()
