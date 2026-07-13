import re
from collections.abc import Iterable

from freqtrade.research.exceptions import ResearchUnsupportedFeatureError


SUPPORTED_A_SHARE_OHLCV_TIMEFRAMES = ("1m", "5m", "15m", "30m", "60m", "1d")
MINUTE_A_SHARE_OHLCV_TIMEFRAMES = ("1m", "5m", "15m", "30m", "60m")

_TIMEFRAME_RE = re.compile(r"^[0-9]+[mhdwM]$")
_TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1d": 1440,
}
_TIMEFRAME_ORDER = {
    timeframe: index for index, timeframe in enumerate(SUPPORTED_A_SHARE_OHLCV_TIMEFRAMES)
}


def validate_a_share_ohlcv_timeframe(timeframe: str) -> str:
    if not timeframe or not _TIMEFRAME_RE.fullmatch(timeframe):
        raise ValueError("Invalid research timeframe")
    if timeframe not in SUPPORTED_A_SHARE_OHLCV_TIMEFRAMES:
        raise ResearchUnsupportedFeatureError(
            f"Research timeframe {timeframe} is not supported yet."
        )
    return timeframe


def is_a_share_minute_timeframe(timeframe: str) -> bool:
    return timeframe in MINUTE_A_SHARE_OHLCV_TIMEFRAMES


def timeframe_to_minutes(timeframe: str) -> int:
    validate_a_share_ohlcv_timeframe(timeframe)
    return _TIMEFRAME_MINUTES[timeframe]


def sort_a_share_ohlcv_timeframes(timeframes: Iterable[str]) -> list[str]:
    return sorted(timeframes, key=lambda timeframe: _TIMEFRAME_ORDER[timeframe])
