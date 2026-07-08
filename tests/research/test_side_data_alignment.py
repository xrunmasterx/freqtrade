from pathlib import Path

import pytest

from freqtrade.markets import CachedAShareCalendar
from freqtrade.research.side_data.alignment import (
    effective_candle_time_for_publish_time,
    is_available_at,
)


def _calendar(tmp_path: Path) -> CachedAShareCalendar:
    path = tmp_path / "trade_dates.csv"
    path.write_text(
        "date,is_open,source\n"
        "2026-07-03,1,test\n"
        "2026-07-04,0,test\n"
        "2026-07-05,0,test\n"
        "2026-07-06,1,test\n",
        encoding="utf-8",
    )
    return CachedAShareCalendar.from_csv(path)


def test_effective_candle_time_uses_same_trading_day_before_close(tmp_path) -> None:
    result = effective_candle_time_for_publish_time(
        "2026-07-03T14:59:00+08:00",
        _calendar(tmp_path),
    )

    assert result == "2026-07-03 00:00:00+00:00"


def test_effective_candle_time_moves_after_close_to_next_trading_day(tmp_path) -> None:
    result = effective_candle_time_for_publish_time(
        "2026-07-03T19:30:00+08:00",
        _calendar(tmp_path),
    )

    assert result == "2026-07-06 00:00:00+00:00"


def test_effective_candle_time_moves_closed_day_to_next_trading_day(tmp_path) -> None:
    result = effective_candle_time_for_publish_time(
        "2026-07-04T10:00:00+08:00",
        _calendar(tmp_path),
    )

    assert result == "2026-07-06 00:00:00+00:00"


def test_effective_candle_time_without_calendar_raises_required_error() -> None:
    with pytest.raises(ValueError, match="calendar is required"):
        effective_candle_time_for_publish_time(
            "2026-07-03T19:30:00+08:00",
            None,
        )


def test_is_available_at_uses_publish_time_not_ingest_time() -> None:
    assert is_available_at("2026-07-07T09:00:00+08:00", "2026-07-07T10:00:00+08:00")
    assert not is_available_at(
        "2026-07-07T11:00:00+08:00",
        "2026-07-07T10:00:00+08:00",
    )
    assert is_available_at(None, "2026-07-07T10:00:00+08:00")
