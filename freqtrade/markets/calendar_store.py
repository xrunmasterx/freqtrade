from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class CachedAShareCalendar:
    open_dates: frozenset[date]
    known_dates: frozenset[date]

    @classmethod
    def from_csv(
        cls,
        path: Path,
        override_closed_dates: set[str] | None = None,
    ) -> CachedAShareCalendar:
        dataframe = pd.read_csv(path)
        required = {"date", "is_open", "source"}
        missing = required - set(dataframe.columns)
        if missing:
            raise ValueError(f"Missing A-share calendar columns: {sorted(missing)}")

        dataframe["date"] = pd.to_datetime(dataframe["date"]).dt.date
        known_dates = set(dataframe["date"])
        open_dates = set(dataframe.loc[dataframe["is_open"].astype(int) == 1, "date"])

        for closed_date in override_closed_dates or set():
            closed_day = pd.Timestamp(closed_date).date()
            open_dates.discard(closed_day)
            known_dates.add(closed_day)

        return cls(frozenset(open_dates), frozenset(known_dates))

    def is_trading_day(self, value: date | datetime | str) -> bool:
        return _to_date(value) in self.open_dates

    def next_trading_day(self, value: date | datetime | str) -> date:
        current = _to_date(value)
        future_open_dates = sorted(day for day in self.open_dates if day > current)
        if not future_open_dates:
            raise ValueError(f"No next A-share trading day after {current.isoformat()}")
        return future_open_dates[0]


def _to_date(value: date | datetime | str) -> date:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        return timestamp.tz_convert(_ASIA_SHANGHAI).date()
    return timestamp.date()
