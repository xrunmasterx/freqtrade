import pytest

from freqtrade.research.a_share_timeframes import (
    MINUTE_A_SHARE_OHLCV_TIMEFRAMES,
    SUPPORTED_A_SHARE_OHLCV_TIMEFRAMES,
    is_a_share_minute_timeframe,
    sort_a_share_ohlcv_timeframes,
    timeframe_to_minutes,
    validate_a_share_ohlcv_timeframe,
)
from freqtrade.research.exceptions import ResearchUnsupportedFeatureError


def test_supported_a_share_timeframe_registry_order() -> None:
    assert SUPPORTED_A_SHARE_OHLCV_TIMEFRAMES == ("1m", "5m", "15m", "30m", "60m", "1d")
    assert MINUTE_A_SHARE_OHLCV_TIMEFRAMES == ("1m", "5m", "15m", "30m", "60m")


@pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "30m", "60m", "1d"])
def test_validate_a_share_ohlcv_timeframe_accepts_supported_values(timeframe: str) -> None:
    assert validate_a_share_ohlcv_timeframe(timeframe) == timeframe


@pytest.mark.parametrize("timeframe", ["3m", "2h", "4h", "1w", "1M"])
def test_validate_a_share_ohlcv_timeframe_rejects_unsupported_values(timeframe: str) -> None:
    with pytest.raises(
        ResearchUnsupportedFeatureError,
        match=f"Research timeframe {timeframe} is not supported yet.",
    ):
        validate_a_share_ohlcv_timeframe(timeframe)


@pytest.mark.parametrize("timeframe", ["", "../1d", "1-day", "abc"])
def test_validate_a_share_ohlcv_timeframe_rejects_invalid_syntax(timeframe: str) -> None:
    with pytest.raises(ValueError, match="Invalid research timeframe"):
        validate_a_share_ohlcv_timeframe(timeframe)


def test_minute_timeframe_detection_and_duration() -> None:
    assert is_a_share_minute_timeframe("1m") is True
    assert is_a_share_minute_timeframe("60m") is True
    assert is_a_share_minute_timeframe("1d") is False
    assert timeframe_to_minutes("1m") == 1
    assert timeframe_to_minutes("5m") == 5
    assert timeframe_to_minutes("15m") == 15
    assert timeframe_to_minutes("30m") == 30
    assert timeframe_to_minutes("60m") == 60
    assert timeframe_to_minutes("1d") == 1440


def test_sort_a_share_ohlcv_timeframes_uses_registry_order() -> None:
    assert sort_a_share_ohlcv_timeframes({"60m", "1d", "1m", "15m", "5m"}) == [
        "1m",
        "5m",
        "15m",
        "60m",
        "1d",
    ]
