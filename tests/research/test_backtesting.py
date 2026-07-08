import math

import pandas as pd
import pytest
from pydantic import ValidationError

from freqtrade.research.backtesting import ResearchBacktestConfig, run_research_backtest
from freqtrade.research.feature_context import (
    ResearchFeatureContext,
    ResearchFeatureFilterConfig,
)


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


def _feature_context(values: list[float | None]) -> ResearchFeatureContext:
    return ResearchFeatureContext(
        instrument="600519.SH",
        datasets=["fund_flow_daily"],
        frame=pd.DataFrame(
            {
                "date": _research_ohlcv_dataframe()["date"],
                "feature_fund_flow_daily_main_net_inflow": values,
            }
        ),
        provenance={"fund_flow_daily": {"provider": "test"}},
    )


def _positive_fund_flow_filter(
    *,
    missing: str = "block",
) -> ResearchFeatureFilterConfig:
    return ResearchFeatureFilterConfig(
        dataset="fund_flow_daily",
        field="main_net_inflow",
        operator=">",
        value=0,
        missing=missing,
    )


def test_run_research_backtest_returns_equity_trades_and_signals() -> None:
    dataframe = _research_ohlcv_dataframe()
    config = ResearchBacktestConfig(
        initial_cash=10000,
        fast=1,
        slow=2,
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
    )

    result = run_research_backtest("600519.SH", dataframe, config)

    assert result.trades[0]["entry_price"] == 12.0
    assert result.trades[0]["exit_price"] == 8.0
    assert result.trades[0]["entry_date"] == "2026-07-09 00:00:00+00:00"
    assert result.trades[0]["exit_date"] == "2026-07-13 00:00:00+00:00"


def test_research_backtest_recomputes_plain_sma_signals_when_signal_columns_exist() -> None:
    dataframe = _research_ohlcv_dataframe()
    dataframe["enter_long"] = [1, 0, 0, 0, 0, 0]
    dataframe["exit_long"] = [0, 1, 0, 0, 0, 0]
    config = ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2)

    result = run_research_backtest(
        "600519.SH",
        dataframe,
        config,
        feature_filter=None,
    )

    assert [signal["type"] for signal in result.signals] == [
        "enter_long",
        "exit_long",
    ]
    assert result.signals[0]["date"] == "2026-07-08 00:00:00+00:00"
    assert result.signals[1]["date"] == "2026-07-10 00:00:00+00:00"
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


def test_research_backtest_enforces_a_share_t_plus_one_for_intraday_data() -> None:
    dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2026-07-06 09:30:00+08:00",
                    "2026-07-06 10:00:00+08:00",
                    "2026-07-06 10:30:00+08:00",
                    "2026-07-06 13:30:00+08:00",
                    "2026-07-06 14:30:00+08:00",
                    "2026-07-07 09:30:00+08:00",
                    "2026-07-07 10:00:00+08:00",
                ],
                utc=True,
            ),
            "open": [12.0, 10.0, 12.0, 12.0, 9.0, 9.0, 8.0],
            "high": [12.5, 10.5, 12.5, 12.5, 12.5, 9.5, 8.5],
            "low": [11.5, 9.5, 11.5, 8.5, 8.5, 7.5, 7.5],
            "close": [12.0, 10.0, 12.0, 9.0, 12.0, 8.0, 8.0],
            "volume": [1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0, 1600.0],
        }
    )
    config = ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2)

    result = run_research_backtest("600519.SH", dataframe, config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    entry_date = pd.Timestamp(trade["entry_date"]).tz_convert("Asia/Shanghai").date()
    exit_date = pd.Timestamp(trade["exit_date"]).tz_convert("Asia/Shanghai").date()
    assert entry_date.isoformat() == "2026-07-06"
    assert exit_date.isoformat() == "2026-07-07"


def test_research_backtest_does_not_defer_same_day_exit_without_new_exit_signal() -> None:
    dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2026-07-06 09:30:00+08:00",
                    "2026-07-06 10:00:00+08:00",
                    "2026-07-06 10:30:00+08:00",
                    "2026-07-06 13:30:00+08:00",
                    "2026-07-06 14:30:00+08:00",
                    "2026-07-07 09:30:00+08:00",
                ],
                utc=True,
            ),
            "open": [12.0, 10.0, 12.0, 12.0, 9.0, 9.0],
            "high": [12.5, 10.5, 12.5, 12.5, 9.5, 9.5],
            "low": [11.5, 9.5, 11.5, 8.5, 8.5, 8.5],
            "close": [12.0, 10.0, 12.0, 9.0, 9.0, 9.0],
            "volume": [1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0],
        }
    )
    config = ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2)

    result = run_research_backtest("600519.SH", dataframe, config)

    assert [signal["type"] for signal in result.signals] == [
        "enter_long",
        "exit_long",
    ]
    assert result.trades == []
    assert result.metrics["position_shares"] > 0


def test_research_backtest_config_rejects_obsolete_economics_fields() -> None:
    with pytest.raises(ValidationError):
        ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2, commission_rate=0)


def test_research_backtest_config_rejects_infinite_initial_cash() -> None:
    with pytest.raises(ValidationError):
        ResearchBacktestConfig(initial_cash=float("inf"), fast=1, slow=2)


def test_research_backtest_blocks_out_of_session_intraday_execution_row() -> None:
    dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2026-07-07T01:30:00Z",
                    "2026-07-07T01:31:00Z",
                    "2026-07-07T01:32:00Z",
                    "2026-07-07T03:30:00Z",
                ],
                utc=True,
            ),
            "open": [10.0, 9.0, 11.0, 12.0],
            "high": [10.5, 9.5, 11.5, 12.5],
            "low": [9.5, 8.5, 10.5, 11.5],
            "close": [10.0, 9.0, 11.0, 12.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )

    result = run_research_backtest(
        "688017.SH",
        dataframe,
        ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2),
    )

    assert any("Blocked fill outside A-share session" in warning for warning in result.warnings)


def test_research_backtest_allows_utc_midnight_daily_rows_without_session_warning() -> None:
    result = run_research_backtest(
        "600519.SH",
        _research_ohlcv_dataframe(),
        ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2),
    )

    assert all(
        "Blocked fill outside A-share session" not in warning for warning in result.warnings
    )


def test_research_backtest_feature_filter_allows_entry_when_condition_passes() -> None:
    result = run_research_backtest(
        "600519.SH",
        _research_ohlcv_dataframe(),
        ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2),
        feature_context=_feature_context([None, None, 1000.0, None, None, None]),
        feature_filter=_positive_fund_flow_filter(),
    )

    assert result.strategy == "sma_cross_feature_filter"
    assert result.metrics["position_shares"] == 0
    assert result.metrics["trade_count"] == 1
    assert result.trades[0]["entry_date"] == "2026-07-09 00:00:00+00:00"
    assert result.trades[0]["exit_date"] == "2026-07-13 00:00:00+00:00"
    assert [signal["type"] for signal in result.signals] == [
        "enter_long",
        "exit_long",
    ]


def test_research_backtest_feature_filter_blocks_entry_when_condition_fails() -> None:
    result = run_research_backtest(
        "600519.SH",
        _research_ohlcv_dataframe(),
        ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2),
        feature_context=_feature_context([None, None, -1.0, None, None, None]),
        feature_filter=_positive_fund_flow_filter(),
    )

    assert result.strategy == "sma_cross_feature_filter"
    assert result.trades == []
    assert result.metrics["position_shares"] == 0
    assert [signal["type"] for signal in result.signals] == ["exit_long"]
    assert any(
        "Feature filter blocked 1 enter_long signal" in warning
        for warning in result.warnings
    )


def test_research_backtest_feature_filter_missing_block_blocks_entry() -> None:
    result = run_research_backtest(
        "600519.SH",
        _research_ohlcv_dataframe(),
        ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2),
        feature_context=_feature_context([None, None, None, None, None, None]),
        feature_filter=_positive_fund_flow_filter(missing="block"),
    )

    assert result.trades == []
    assert any("missing feature value" in warning for warning in result.warnings)


def test_research_backtest_feature_filter_missing_allow_preserves_entry() -> None:
    result = run_research_backtest(
        "600519.SH",
        _research_ohlcv_dataframe(),
        ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2),
        feature_context=_feature_context([None, None, None, None, None, None]),
        feature_filter=_positive_fund_flow_filter(missing="allow"),
    )

    assert result.metrics["trade_count"] == 1
    assert [signal["type"] for signal in result.signals] == [
        "enter_long",
        "exit_long",
    ]


def test_research_backtest_feature_filter_requires_feature_context() -> None:
    with pytest.raises(ValueError, match=r"Feature filter requires ResearchFeatureContext"):
        run_research_backtest(
            "600519.SH",
            _research_ohlcv_dataframe(),
            ResearchBacktestConfig(initial_cash=10000, fast=1, slow=2),
            feature_filter=_positive_fund_flow_filter(),
        )
