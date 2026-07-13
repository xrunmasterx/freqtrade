import pandas as pd
import pytest

from freqtrade.research.a_share_sessions import (
    is_a_share_regular_session_timestamp,
    validate_a_share_regular_session_frame,
)
from freqtrade.research.exceptions import ResearchUnsupportedFeatureError


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-07T01:30:00Z",
        "2026-07-07T03:29:00Z",
        "2026-07-07T05:00:00Z",
        "2026-07-07T06:59:00Z",
    ],
)
def test_is_a_share_regular_session_timestamp_accepts_open_minutes(timestamp: str) -> None:
    assert is_a_share_regular_session_timestamp(timestamp) is True


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-07T01:29:00Z",
        "2026-07-07T03:30:00Z",
        "2026-07-07T04:00:00Z",
        "2026-07-07T07:00:00Z",
    ],
)
def test_is_a_share_regular_session_timestamp_rejects_closed_minutes(timestamp: str) -> None:
    assert is_a_share_regular_session_timestamp(timestamp) is False


def test_validate_a_share_regular_session_frame_allows_daily_timeframe() -> None:
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-07-07"], utc=True)})

    validate_a_share_regular_session_frame(frame, "1d")


def test_validate_a_share_regular_session_frame_rejects_out_of_session_minute_row() -> None:
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-07-07T03:30:00Z"], utc=True)})

    with pytest.raises(
        ValueError,
        match=r"A-share minute OHLCV contains out-of-session rows: 2026-07-07T03:30:00Z",
    ):
        validate_a_share_regular_session_frame(frame, "1m")


@pytest.mark.parametrize("timeframe", ["1h", "qfq", "hfq", "3m"])
def test_validate_a_share_regular_session_frame_rejects_unsupported_timeframe(
    timeframe: str,
) -> None:
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-07-07"], utc=True)})

    with pytest.raises(
        (ResearchUnsupportedFeatureError, ValueError),
        match=r"Research timeframe|Invalid research timeframe",
    ):
        validate_a_share_regular_session_frame(frame, timeframe)
