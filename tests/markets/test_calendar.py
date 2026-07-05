from datetime import UTC, datetime

from freqtrade.markets.calendar import AShareCalendar


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
