from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from freqtrade.markets.calendar_store import CachedAShareCalendar


def test_cached_calendar_reads_trading_days_and_closed_overrides(tmp_path) -> None:
    path = tmp_path / "a_share_trade_dates.csv"
    path.write_text(
        "date,is_open,source\n"
        "2024-02-08,1,sina\n"
        "2024-02-09,1,sina\n"
        "2024-02-10,0,sina\n"
        "2024-02-19,1,sina\n",
        encoding="utf-8",
    )

    calendar = CachedAShareCalendar.from_csv(path, override_closed_dates={"2024-02-09"})

    assert calendar.is_trading_day("2024-02-08") is True
    assert calendar.is_trading_day("2024-02-09") is False
    assert calendar.is_trading_day("2024-02-10") is False
    assert calendar.next_trading_day("2024-02-08") == date(2024, 2, 19)


def test_cached_calendar_accepts_timezone_aware_datetime(tmp_path) -> None:
    path = tmp_path / "a_share_trade_dates.csv"
    path.write_text("date,is_open,source\n2024-01-02,1,sina\n", encoding="utf-8")
    calendar = CachedAShareCalendar.from_csv(path)

    assert calendar.is_trading_day(
        datetime(2024, 1, 2, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )


def test_cached_calendar_converts_aware_datetime_to_shanghai_date(tmp_path) -> None:
    path = tmp_path / "a_share_trade_dates.csv"
    path.write_text(
        "date,is_open,source\n"
        "2024-01-02,1,sina\n"
        "2024-01-03,0,sina\n",
        encoding="utf-8",
    )
    calendar = CachedAShareCalendar.from_csv(path)

    assert calendar.is_trading_day(datetime(2024, 1, 2, 16, 30, tzinfo=ZoneInfo("UTC"))) is False


def test_cached_calendar_rejects_next_day_when_no_future_session(tmp_path) -> None:
    path = tmp_path / "a_share_trade_dates.csv"
    path.write_text("date,is_open,source\n2024-01-02,1,sina\n", encoding="utf-8")
    calendar = CachedAShareCalendar.from_csv(path)

    with pytest.raises(ValueError, match="No next A-share trading day after 2024-01-02"):
        calendar.next_trading_day("2024-01-02")


def test_cached_calendar_rejects_missing_columns(tmp_path) -> None:
    path = tmp_path / "a_share_trade_dates.csv"
    path.write_text("date,is_open\n2024-01-02,1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing A-share calendar columns"):
        CachedAShareCalendar.from_csv(path)
