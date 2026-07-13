import pandas as pd

from freqtrade.markets import AShareStatusStore, CachedAShareCalendar
from freqtrade.research.backtesting import (
    ResearchBacktestConfig,
    ResearchMarketContext,
    run_research_backtest,
)


def _dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2023-12-31", "2024-01-01", "2024-01-02", "2024-01-03"],
                utc=True,
            ),
            "open": [101.0, 100.0, 110.0, 110.0],
            "high": [102.0, 101.0, 111.0, 111.0],
            "low": [100.0, 99.0, 109.0, 109.0],
            "close": [101.0, 100.0, 110.0, 110.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )


def _intraday_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2024-01-02 09:30:00+08:00",
                    "2024-01-02 10:00:00+08:00",
                    "2024-01-02 10:30:00+08:00",
                    "2024-01-02 13:00:00+08:00",
                    "2024-01-02 14:00:00+08:00",
                ],
                utc=True,
            ),
            "open": [101.0, 100.0, 110.0, 100.0, 100.0],
            "high": [102.0, 101.0, 111.0, 101.0, 101.0],
            "low": [100.0, 99.0, 109.0, 99.0, 99.0],
            "close": [101.0, 100.0, 110.0, 100.0, 100.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
        }
    )


def _closed_day_buy_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2023-12-31", "2024-01-01", "2024-01-02", "2024-01-03"],
                utc=True,
            ),
            "open": [101.0, 100.0, 110.0, 110.0],
            "high": [102.0, 101.0, 111.0, 111.0],
            "low": [100.0, 99.0, 109.0, 109.0],
            "close": [101.0, 100.0, 110.0, 110.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )


def _closed_day_sell_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2023-12-31",
                    "2024-01-01",
                    "2024-01-02",
                    "2024-01-04",
                    "2024-01-06",
                    "2024-01-08",
                ],
                utc=True,
            ),
            "open": [101.0, 100.0, 110.0, 90.0, 80.0, 80.0],
            "high": [102.0, 101.0, 111.0, 91.0, 81.0, 81.0],
            "low": [100.0, 99.0, 109.0, 89.0, 79.0, 79.0],
            "close": [101.0, 100.0, 110.0, 90.0, 80.0, 80.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
        }
    )


def test_research_backtest_blocks_buy_on_limit_up(tmp_path) -> None:
    status_path = tmp_path / "status.csv"
    status_path.write_text(
        "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date,source\n"
        "2024-01-03,600519.SH,0,110.0,90.0,1000,2001-08-27,,test\n",
        encoding="utf-8",
    )
    context = ResearchMarketContext(
        status_store=AShareStatusStore.from_csv(status_path),
    )

    result = run_research_backtest(
        "600519.SH",
        _dataframe(),
        ResearchBacktestConfig(initial_cash=100000, fast=1, slow=2),
        market_context=context,
    )

    assert result.metrics["trade_count"] == 0
    assert len(result.equity_curve) == len(_dataframe())
    assert "Blocked buy fill on 2024-01-03 00:00:00+00:00" in result.warnings


def test_research_backtest_uses_trading_day_t_plus_one(tmp_path) -> None:
    calendar_path = tmp_path / "calendar.csv"
    calendar_path.write_text(
        "date,is_open,source\n"
        "2024-01-02,1,test\n"
        "2024-01-03,1,test\n",
        encoding="utf-8",
    )
    context = ResearchMarketContext(
        calendar=CachedAShareCalendar.from_csv(calendar_path),
    )

    result = run_research_backtest(
        "600519.SH",
        _intraday_dataframe(),
        ResearchBacktestConfig(initial_cash=100000, fast=1, slow=2),
        market_context=context,
    )

    assert result.metrics["trade_count"] == 0
    assert len(result.equity_curve) == len(_intraday_dataframe())
    assert any("T+1" in warning for warning in result.warnings)


def test_research_backtest_blocks_buy_on_non_trading_day(tmp_path) -> None:
    calendar_path = tmp_path / "calendar.csv"
    calendar_path.write_text(
        "date,is_open,source\n"
        "2024-01-02,1,test\n"
        "2024-01-03,0,test\n"
        "2024-01-04,1,test\n",
        encoding="utf-8",
    )
    context = ResearchMarketContext(calendar=CachedAShareCalendar.from_csv(calendar_path))

    result = run_research_backtest(
        "600519.SH",
        _closed_day_buy_dataframe(),
        ResearchBacktestConfig(initial_cash=100000, fast=1, slow=2),
        market_context=context,
    )

    assert result.metrics["trade_count"] == 0
    assert result.metrics["position_shares"] == 0
    assert len(result.equity_curve) == len(_closed_day_buy_dataframe())
    assert "Blocked fill on non-trading day 2024-01-03 00:00:00+00:00" in result.warnings


def test_research_backtest_blocks_sell_on_non_trading_day_and_updates_final_equity(
    tmp_path,
) -> None:
    calendar_path = tmp_path / "calendar.csv"
    calendar_path.write_text(
        "date,is_open,source\n"
        "2024-01-02,1,test\n"
        "2024-01-04,1,test\n"
        "2024-01-06,0,test\n"
        "2024-01-08,1,test\n",
        encoding="utf-8",
    )
    context = ResearchMarketContext(calendar=CachedAShareCalendar.from_csv(calendar_path))

    result = run_research_backtest(
        "600519.SH",
        _closed_day_sell_dataframe(),
        ResearchBacktestConfig(initial_cash=100000, fast=1, slow=2),
        market_context=context,
    )

    assert result.metrics["trade_count"] == 0
    assert result.metrics["position_shares"] > 0
    assert len(result.equity_curve) == len(_closed_day_sell_dataframe())
    assert result.metrics["final_equity"] == result.equity_curve[-1]["equity"]
    assert "Blocked fill on non-trading day 2024-01-06 00:00:00+00:00" in result.warnings
