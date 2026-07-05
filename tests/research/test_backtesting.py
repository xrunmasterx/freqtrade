import math

import pandas as pd
import pytest
from pydantic import ValidationError

from freqtrade.research.backtesting import ResearchBacktestConfig, run_research_backtest


def _research_ohlcv_dataframe(
    open_values: list[float] | None = None,
    close_values: list[float] | None = None,
) -> pd.DataFrame:
    if open_values is None:
        open_values = [10.0, 9.0, 11.0, 12.0, 10.0, 8.0]
    if close_values is None:
        close_values = [10.0, 9.0, 11.0, 12.0, 10.0, 8.0]

    return pd.DataFrame(
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
            "open": open_values,
            "high": [
                max(open_, close_) + 0.5
                for open_, close_ in zip(open_values, close_values, strict=True)
            ],
            "low": [
                min(open_, close_) - 0.5
                for open_, close_ in zip(open_values, close_values, strict=True)
            ],
            "close": close_values,
            "volume": [1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0],
        }
    )


def test_run_research_backtest_returns_equity_trades_and_signals() -> None:
    dataframe = _research_ohlcv_dataframe()
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
    assert math.isfinite(result.metrics["return_ratio"])
    assert len(result.equity_curve) == len(dataframe)
    assert result.metrics["final_equity"] > 0
    assert isinstance(result.trades, list)
    signal_types = {signal["type"] for signal in result.signals}
    assert {"enter_long", "exit_long"} <= signal_types


def test_research_backtest_executes_signals_on_next_open() -> None:
    dataframe = _research_ohlcv_dataframe(
        open_values=[10.0, 9.0, 13.0, 12.0, 7.0, 8.0],
        close_values=[10.0, 9.0, 11.0, 12.0, 10.0, 8.0],
    )
    config = ResearchBacktestConfig(
        initial_cash=10000,
        fast=1,
        slow=2,
        lot_size=100,
        commission_rate=0,
        stamp_tax_rate=0,
    )

    result = run_research_backtest("600519.SH", dataframe, config)

    assert result.trades[0]["entry_price"] == 12.0
    assert result.trades[0]["exit_price"] == 8.0
    assert result.trades[0]["entry_date"] == "2026-07-09 00:00:00+00:00"
    assert result.trades[0]["exit_date"] == "2026-07-13 00:00:00+00:00"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("close", float("nan")),
        ("open", float("inf")),
        ("close", float("-inf")),
    ],
)
def test_research_backtest_rejects_non_finite_prices(column: str, value: float) -> None:
    dataframe = _research_ohlcv_dataframe()
    dataframe.loc[2, column] = value
    config = ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2)

    with pytest.raises(ValueError):
        run_research_backtest("600519.SH", dataframe, config)


def test_research_backtest_rejects_invalid_period_order() -> None:
    with pytest.raises((ValueError, ValidationError)):
        ResearchBacktestConfig(initial_cash=10000, fast=2, slow=2)


def test_research_backtest_config_rejects_infinite_initial_cash() -> None:
    with pytest.raises(ValidationError):
        ResearchBacktestConfig(initial_cash=float("inf"), fast=1, slow=2)
