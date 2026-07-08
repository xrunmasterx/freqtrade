from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, ConfigDict

from freqtrade.markets.instrument import MarketType, parse_instrument_key


_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")


class AShareDailyStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: str
    instrument: str
    suspended: bool
    limit_up: float | None
    limit_down: float | None
    volume: float | None
    listed_date: str | None
    delisted_date: str | None
    source: str


class AShareStatusStore:
    def __init__(self, statuses: dict[tuple[str, str], AShareDailyStatus]) -> None:
        self._statuses = statuses

    @classmethod
    def from_csv(cls, path: Path) -> AShareStatusStore:
        dataframe = pd.read_csv(path)
        required = {
            "date",
            "instrument",
            "suspended",
            "limit_up",
            "limit_down",
            "volume",
            "listed_date",
            "delisted_date",
            "source",
        }
        missing = required - set(dataframe.columns)
        if missing:
            raise ValueError(f"Missing A-share status columns: {sorted(missing)}")

        statuses: dict[tuple[str, str], AShareDailyStatus] = {}
        for row in dataframe.to_dict("records"):
            instrument = parse_instrument_key(
                str(row["instrument"]),
                market=MarketType.A_SHARE,
            ).key
            trading_date = _to_shanghai_date(row["date"]).isoformat()
            status = AShareDailyStatus(
                date=trading_date,
                instrument=instrument,
                suspended=bool(int(row["suspended"])),
                limit_up=_optional_float(row["limit_up"]),
                limit_down=_optional_float(row["limit_down"]),
                volume=_optional_float(row["volume"]),
                listed_date=_optional_date(row["listed_date"]),
                delisted_date=_optional_date(row["delisted_date"]),
                source=str(row["source"]),
            )
            statuses[(instrument, trading_date)] = status

        return cls(statuses)

    def get_status(
        self,
        instrument_key: str,
        value: date | datetime | str,
    ) -> AShareDailyStatus | None:
        instrument = parse_instrument_key(instrument_key, market=MarketType.A_SHARE).key
        trading_date = _to_shanghai_date(value).isoformat()
        return self._statuses.get((instrument, trading_date))


def _optional_float(value: object) -> float | None:
    if pd.isna(value) or value == "":
        return None
    return float(value)


def _optional_date(value: object) -> str | None:
    if pd.isna(value) or value == "":
        return None
    return _to_shanghai_date(value).isoformat()


def _to_shanghai_date(value: object) -> date:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(_ASIA_SHANGHAI)
    return timestamp.date()
