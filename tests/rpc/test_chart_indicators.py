import pytest
from pandas.testing import assert_frame_equal

from freqtrade.rpc.api_server.api_schemas import ChartIndicatorRequest, MacdIndicatorRequest
from freqtrade.rpc.chart_indicators import add_watch_indicators, build_watch_plot_config
from tests.conftest import generate_test_data


def test_add_watch_indicators_uses_default_columns():
    dataframe = generate_test_data("15m", 120, "2024-01-01 00:00:00+00:00")
    original = dataframe.copy()

    result = add_watch_indicators(dataframe)

    assert_frame_equal(dataframe, original)
    assert "watch_ma20" in result.columns
    assert "watch_ma60" in result.columns
    assert "watch_rsi14" in result.columns
    assert "watch_macd" in result.columns
    assert "watch_macdsignal" in result.columns
    assert "watch_macdhist" in result.columns
    assert result["watch_ma20"].notna().sum() > 0
    assert result["watch_ma60"].notna().sum() > 0
    assert result["watch_rsi14"].notna().sum() > 0
    assert result["watch_macd"].notna().sum() > 0
    assert result.loc[19, "watch_ma20"] == pytest.approx(
        dataframe["close"].rolling(20).mean().loc[19]
    )


def test_add_watch_indicators_accepts_custom_periods():
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00")
    indicators = ChartIndicatorRequest(
        ma=[10],
        rsi=[7],
        macd=[MacdIndicatorRequest(fast=5, slow=13, signal=4)],
    )

    result = add_watch_indicators(dataframe, indicators)

    assert "watch_ma10" in result.columns
    assert "watch_ma20" not in result.columns
    assert "watch_rsi7" in result.columns
    assert "watch_macd_5_13_4" in result.columns
    assert "watch_macdsignal_5_13_4" in result.columns
    assert "watch_macdhist_5_13_4" in result.columns


def test_build_watch_plot_config_matches_default_columns():
    plot_config = build_watch_plot_config()

    assert set(plot_config["main_plot"]) == {"watch_ma20", "watch_ma60"}
    assert set(plot_config["subplots"]["RSI 14"]) == {"watch_rsi14"}
    assert set(plot_config["subplots"]["MACD"]) == {
        "watch_macd",
        "watch_macdsignal",
        "watch_macdhist",
    }
    assert plot_config["subplots"]["MACD"]["watch_macdhist"]["type"] == "bar"


def test_build_watch_plot_config_explicit_default_matches_none_default():
    assert build_watch_plot_config(ChartIndicatorRequest()) == build_watch_plot_config()


def test_build_watch_plot_config_accepts_custom_periods():
    indicators = ChartIndicatorRequest(
        ma=[10],
        rsi=[7],
        macd=[MacdIndicatorRequest(fast=5, slow=13, signal=4)],
    )

    plot_config = build_watch_plot_config(indicators)

    assert set(plot_config["main_plot"]) == {"watch_ma10"}
    assert set(plot_config["subplots"]["RSI 7"]) == {"watch_rsi7"}
    assert set(plot_config["subplots"]["MACD"]) == {
        "watch_macd_5_13_4",
        "watch_macdsignal_5_13_4",
        "watch_macdhist_5_13_4",
    }
    assert plot_config["subplots"]["MACD"]["watch_macdhist_5_13_4"]["type"] == "bar"


def test_add_watch_indicators_accepts_empty_request():
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00")
    indicators = ChartIndicatorRequest(ma=[], rsi=[], macd=[])

    result = add_watch_indicators(dataframe, indicators)

    assert list(result.columns) == ["date", "open", "high", "low", "close", "volume"]


def test_invalid_macd_period_order_fails_schema():
    with pytest.raises(ValueError, match="slow period"):
        MacdIndicatorRequest(fast=26, slow=12, signal=9)
