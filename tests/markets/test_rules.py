import pandas as pd
import pytest

from freqtrade.markets import AShareDailyStatus, AShareMarketRules, CachedAShareCalendar


def _status(**overrides) -> AShareDailyStatus:
    values = {
        "date": "2024-01-02",
        "instrument": "600519.SH",
        "suspended": False,
        "limit_up": 110.0,
        "limit_down": 90.0,
        "volume": 1000.0,
        "listed_date": "2001-08-27",
        "delisted_date": None,
        "source": "test",
    }
    values.update(overrides)
    return AShareDailyStatus(**values)


def test_a_share_rules_block_same_day_sell() -> None:
    rules = AShareMarketRules()

    assert (
        rules.can_sell(
            pd.Timestamp("2026-07-06 10:00:00+08:00"),
            pd.Timestamp("2026-07-06 14:00:00+08:00"),
        )
        is False
    )


def test_a_share_rules_allow_next_trading_date_sell() -> None:
    rules = AShareMarketRules()

    assert (
        rules.can_sell(
            pd.Timestamp("2026-07-06 10:00:00+08:00"),
            pd.Timestamp("2026-07-07 09:30:00+08:00"),
        )
        is True
    )


def test_a_share_rules_round_to_whole_lots() -> None:
    rules = AShareMarketRules()

    assert rules.whole_lot_shares(cash=10000, price=12.3) == 800


def test_market_rules_reject_suspended_and_zero_volume_fill() -> None:
    rules = AShareMarketRules()

    assert rules.can_fill_order("buy", 100.0, _status(suspended=True)) is False
    assert rules.can_fill_order("buy", 100.0, _status(volume=0.0)) is False


def test_market_rules_reject_limit_up_buy_and_limit_down_sell() -> None:
    rules = AShareMarketRules()

    assert rules.can_fill_order("buy", 110.0, _status()) is False
    assert rules.can_fill_order("sell", 90.0, _status()) is False
    assert rules.can_fill_order("buy", 109.99, _status()) is True
    assert rules.can_fill_order("sell", 90.01, _status()) is True


def test_market_rules_reject_unlisted_or_delisted_status() -> None:
    rules = AShareMarketRules()

    assert rules.can_fill_order("buy", 100.0, _status(listed_date="2024-01-03")) is False
    assert rules.can_fill_order("sell", 100.0, _status(delisted_date="2024-01-01")) is False


def test_market_rules_allow_fill_without_status() -> None:
    rules = AShareMarketRules()

    assert rules.can_fill_order("buy", 100.0, None) is True


def test_market_rules_reject_unsupported_side() -> None:
    rules = AShareMarketRules()

    with pytest.raises(ValueError, match="Unsupported A-share order side: hold"):
        rules.can_fill_order("hold", 100.0, _status())

    with pytest.raises(ValueError, match="Unsupported A-share order side: hold"):
        rules.can_fill_order("hold", 100.0, None)

    with pytest.raises(ValueError, match="Unsupported A-share order side: hold"):
        rules.can_fill_order("hold", 100.0, _status(suspended=True))


def test_rules_allow_sell_on_next_trading_day_when_calendar_skips_closed_day(tmp_path) -> None:
    path = tmp_path / "a_share_trade_dates.csv"
    path.write_text(
        "date,is_open,source\n"
        "2024-01-02,1,test\n"
        "2024-01-03,0,test\n"
        "2024-01-04,1,test\n",
        encoding="utf-8",
    )
    rules = AShareMarketRules()
    calendar = CachedAShareCalendar.from_csv(path)

    assert rules.can_sell("2024-01-02", "2024-01-03", calendar=calendar) is False
    assert rules.can_sell("2024-01-02", "2024-01-04", calendar=calendar) is True
