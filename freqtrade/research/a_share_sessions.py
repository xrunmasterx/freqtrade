from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pandas import DataFrame

from freqtrade.research.a_share_timeframes import (
    is_a_share_minute_timeframe,
    validate_a_share_ohlcv_timeframe,
)


_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MORNING_START = time(9, 30)
_MORNING_END = time(11, 30)
_AFTERNOON_START = time(13, 0)
_AFTERNOON_END = time(15, 0)


def is_a_share_regular_session_timestamp(value: Any) -> bool:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(_ASIA_SHANGHAI)
    else:
        timestamp = timestamp.tz_convert(_ASIA_SHANGHAI)

    local_time = timestamp.time()
    return (
        _MORNING_START <= local_time < _MORNING_END
        or _AFTERNOON_START <= local_time < _AFTERNOON_END
    )


def validate_a_share_regular_session_frame(dataframe: DataFrame, timeframe: str) -> None:
    validate_a_share_ohlcv_timeframe(timeframe)
    if not is_a_share_minute_timeframe(timeframe):
        return

    dates = pd.to_datetime(dataframe["date"], utc=True, errors="raise")
    invalid = [
        value.strftime("%Y-%m-%dT%H:%M:%SZ")
        for value in dates
        if not is_a_share_regular_session_timestamp(value)
    ]
    if invalid:
        raise ValueError(
            "A-share minute OHLCV contains out-of-session rows: " + ", ".join(invalid[:3])
        )
