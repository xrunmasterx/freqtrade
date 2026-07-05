import pandas as pd
import pytest
from pydantic import ValidationError

from freqtrade.research.backtesting import ResearchBacktestConfig, run_research_backtest


def test_run_research_backtest_returns_equity_trades_and_signals() -> None:
    dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2026-07-06",
                    "2026-07-07",
                    "2026-07-08",
                    "2026-07-09",
                    "2026-07-10",
                    "2026-07-13",
                ],
                utc=True,
            ),
            "open": [10.0, 9.0, 11.0, 12.0, 10.0, 8.0],
            "high": [10.5, 9.5, 11.5, 12.5, 10.5, 8.5],
            "low": [9.5, 8.5, 10.5, 11.5, 9.5, 7.5],
            "close": [10.0, 9.0, 11.0, 12.0, 10.0, 8.0],
            "volume": [1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0],
        }
    )
    config = ResearchBacktestConfig(
        initial_cash=10000,
        fast=1,
        slow=2,
        lot_size=100,
        commission_rate=0.0003,
        stamp_tax_rate=0.001,
    )

    result = run_research_backtest("600519.SH", dataframe, config)

    assert result.metrics["initial_cash"] == 10000
    assert "return_ratio" in result.metrics
    assert len(result.equity_curve) == len(dataframe)
    assert result.metrics["final_equity"] > 0
    assert isinstance(result.trades, list)
    signal_types = {signal["type"] for signal in result.signals}
    assert {"enter_long", "exit_long"} <= signal_types


def test_research_backtest_rejects_invalid_period_order() -> None:
    with pytest.raises((ValueError, ValidationError)):
        ResearchBacktestConfig(initial_cash=10000, fast=2, slow=2)
