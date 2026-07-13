from datetime import UTC, datetime

from freqtrade.markets.calendar import AShareCalendar
from freqtrade.markets.calendar_store import CachedAShareCalendar


def test_a_share_calendar_is_open_during_morning_session() -> None:
    calendar = AShareCalendar()

    assert calendar.is_session_open(datetime(2026, 7, 6, 1, 45, tzinfo=UTC)) is True


def test_a_share_calendar_treats_naive_datetime_as_shanghai_time() -> None:
    calendar = AShareCalendar()

    assert calendar.is_session_open(datetime(2026, 7, 6, 9, 45)) is True


def test_a_share_calendar_is_closed_during_lunch_break() -> None:
    calendar = AShareCalendar()

    assert calendar.is_session_open(datetime(2026, 7, 6, 4, 0, tzinfo=UTC)) is False


def test_a_share_calendar_is_closed_on_weekends() -> None:
    calendar = AShareCalendar()

    assert calendar.is_session_open(datetime(2026, 7, 5, 1, 45, tzinfo=UTC)) is False


def test_a_share_calendar_uses_cached_calendar_for_trading_days(tmp_path) -> None:
    path = tmp_path / "a_share_trade_dates.csv"
    path.write_text(
        "date,is_open,source\n"
        "2024-02-08,1,sina\n"
        "2024-02-09,1,sina\n"
        "2024-02-10,0,sina\n"
        "2024-02-19,1,sina\n",
        encoding="utf-8",
    )
    cached_calendar = CachedAShareCalendar.from_csv(
        path,
        override_closed_dates={"2024-02-09"},
    )
    calendar = AShareCalendar(cached_calendar=cached_calendar)

    assert calendar.is_trading_day("2024-02-08") is True
    assert calendar.is_trading_day("2024-02-09") is False
    assert calendar.next_trading_day("2024-02-08") == datetime(2024, 2, 19).date()


def test_a_share_calendar_next_trading_day_uses_shanghai_date_for_aware_input() -> None:
    calendar = AShareCalendar(closed_dates={"2024-01-03"})

    assert calendar.next_trading_day(datetime(2024, 1, 1, 16, 30, tzinfo=UTC)) == datetime(
        2024, 1, 4
    ).date()
