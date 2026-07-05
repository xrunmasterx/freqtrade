import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from freqtrade.rpc.api_server.api_schemas import (
    ChartIndicatorRequest,
    MacdIndicatorRequest,
    QqeModIndicatorRequest,
    SupertrendIndicatorRequest,
)
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
    assert "watch_supertrend_up" in result.columns
    assert "watch_supertrend_down" in result.columns
    assert "watch_supertrend_price" in result.columns
    assert "watch_qqe_mod_trend" in result.columns
    assert "watch_qqe_mod_hist" in result.columns
    assert "watch_qqe_mod_up" in result.columns
    assert "watch_qqe_mod_down" in result.columns
    assert "watch_qqe_mod_up_state" in result.columns
    assert "watch_qqe_mod_down_state" in result.columns
    assert "watch_qqe_mod_up_event" in result.columns
    assert "watch_qqe_mod_down_event" in result.columns
    assert result["watch_ma20"].notna().sum() > 0
    assert result["watch_ma60"].notna().sum() > 0
    assert result["watch_rsi14"].notna().sum() > 0
    assert result["watch_macd"].notna().sum() > 0
    assert result["watch_qqe_mod_hist"].notna().sum() > 0
    assert (
        result[["watch_supertrend_up", "watch_supertrend_down"]]
        .notna()
        .any(axis=1)
        .sum()
        > 0
    )
    assert_series_equal(result["watch_supertrend_price"], dataframe["close"], check_names=False)
    assert result.loc[19, "watch_ma20"] == pytest.approx(
        dataframe["close"].rolling(20).mean().loc[19]
    )


def test_add_watch_indicators_accepts_custom_periods():
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00")
    indicators = ChartIndicatorRequest(
        ma=[10],
        rsi=[7],
        macd=[MacdIndicatorRequest(fast=5, slow=13, signal=4)],
        supertrend=[SupertrendIndicatorRequest(period=7, multiplier=2.5)],
        qqe_mod=[],
    )

    result = add_watch_indicators(dataframe, indicators)

    assert "watch_ma10" in result.columns
    assert "watch_ma20" not in result.columns
    assert "watch_rsi7" in result.columns
    assert "watch_macd_5_13_4" in result.columns
    assert "watch_macdsignal_5_13_4" in result.columns
    assert "watch_macdhist_5_13_4" in result.columns
    assert "watch_supertrend_up_7_2_5" in result.columns
    assert "watch_supertrend_down_7_2_5" in result.columns
    assert "watch_supertrend_price_7_2_5" in result.columns
    assert_series_equal(
        result["watch_supertrend_price_7_2_5"], dataframe["close"], check_names=False
    )


def test_build_watch_plot_config_matches_default_columns():
    plot_config = build_watch_plot_config()

    assert set(plot_config["main_plot"]) == {
        "watch_ma20",
        "watch_ma60",
        "watch_supertrend_up",
        "watch_supertrend_down",
        "watch_supertrend_price",
    }
    assert plot_config["main_plot"]["watch_supertrend_up"] == {
        "color": "#22c55e",
        "type": "line",
        "fill_to": "watch_supertrend_price",
    }
    assert plot_config["main_plot"]["watch_supertrend_down"] == {
        "color": "#ef4444",
        "type": "line",
        "fill_to": "watch_supertrend_price",
    }
    assert plot_config["main_plot"]["watch_supertrend_price"] == {
        "type": "line",
        "hidden": True,
    }
    assert set(plot_config["subplots"]["RSI 14"]) == {"watch_rsi14"}
    assert set(plot_config["subplots"]["MACD"]) == {
        "watch_macd",
        "watch_macdsignal",
        "watch_macdhist",
    }
    assert plot_config["subplots"]["MACD"]["watch_macdhist"]["type"] == "bar"
    assert set(plot_config["subplots"]["QQE MOD"]) == {
        "watch_qqe_mod_hist",
        "watch_qqe_mod_up",
        "watch_qqe_mod_down",
        "watch_qqe_mod_trend",
    }
    assert plot_config["subplots"]["QQE MOD"]["watch_qqe_mod_hist"] == {
        "type": "bar",
        "color": "#64748b",
    }
    assert plot_config["subplots"]["QQE MOD"]["watch_qqe_mod_up"] == {
        "type": "bar",
        "color": "#22c55e",
    }
    assert plot_config["subplots"]["QQE MOD"]["watch_qqe_mod_down"] == {
        "type": "bar",
        "color": "#ef4444",
    }
    assert plot_config["subplots"]["QQE MOD"]["watch_qqe_mod_trend"] == {
        "type": "line",
        "color": "#eab308",
    }


def test_supertrend_populates_only_one_direction_per_candle():
    dataframe = generate_test_data("15m", 160, "2024-01-01 00:00:00+00:00")

    result = add_watch_indicators(dataframe)

    populated_sides = result[["watch_supertrend_up", "watch_supertrend_down"]].notna().sum(axis=1)
    assert populated_sides.max() <= 1
    assert populated_sides.sum() > 0


def test_supertrend_matches_expected_bands_after_direction_transition():
    dataframe = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=5, freq="15min", tz="UTC"),
            "open": [10.0, 10.0, 10.0, 12.0, 13.0],
            "high": [10.0, 10.0, 10.0, 12.0, 13.0],
            "low": [10.0, 10.0, 10.0, 12.0, 13.0],
            "close": [10.0, 10.0, 10.0, 12.0, 13.0],
            "volume": [100.0, 100.0, 100.0, 100.0, 100.0],
        }
    )
    indicators = ChartIndicatorRequest(
        ma=[],
        rsi=[],
        macd=[],
        supertrend=[SupertrendIndicatorRequest(period=2, multiplier=1.0)],
        qqe_mod=[],
    )

    result = add_watch_indicators(dataframe, indicators)

    expected_up = pd.Series([float("nan"), float("nan"), float("nan"), 11.0, 12.0])
    expected_down = pd.Series([float("nan"), float("nan"), 10.0, float("nan"), float("nan")])
    assert_series_equal(result["watch_supertrend_up_2_1"], expected_up, check_names=False)
    assert_series_equal(result["watch_supertrend_down_2_1"], expected_down, check_names=False)


def test_build_watch_plot_config_explicit_default_matches_none_default():
    assert build_watch_plot_config(ChartIndicatorRequest()) == build_watch_plot_config()


def test_build_watch_plot_config_accepts_custom_periods():
    indicators = ChartIndicatorRequest(
        ma=[10],
        rsi=[7],
        macd=[MacdIndicatorRequest(fast=5, slow=13, signal=4)],
        supertrend=[SupertrendIndicatorRequest(period=7, multiplier=2.5)],
        qqe_mod=[],
    )

    plot_config = build_watch_plot_config(indicators)

    assert set(plot_config["main_plot"]) == {
        "watch_ma10",
        "watch_supertrend_up_7_2_5",
        "watch_supertrend_down_7_2_5",
        "watch_supertrend_price_7_2_5",
    }
    assert plot_config["main_plot"]["watch_supertrend_up_7_2_5"]["fill_to"] == (
        "watch_supertrend_price_7_2_5"
    )
    assert plot_config["main_plot"]["watch_supertrend_price_7_2_5"]["hidden"] is True
    assert set(plot_config["subplots"]["RSI 7"]) == {"watch_rsi7"}
    assert set(plot_config["subplots"]["MACD"]) == {
        "watch_macd_5_13_4",
        "watch_macdsignal_5_13_4",
        "watch_macdhist_5_13_4",
    }
    assert plot_config["subplots"]["MACD"]["watch_macdhist_5_13_4"]["type"] == "bar"


def test_add_watch_indicators_accepts_custom_qqe_mod_periods():
    dataframe = generate_test_data("15m", 120, "2024-01-01 00:00:00+00:00")
    indicators = ChartIndicatorRequest(
        ma=[],
        rsi=[],
        macd=[],
        supertrend=[],
        qqe_mod=[QqeModIndicatorRequest(rsi_length=7, threshold=4.0)],
    )

    result = add_watch_indicators(dataframe, indicators)

    suffix = "7_5_3_50_0_35_6_5_1_61_4_close"
    assert f"watch_qqe_mod_trend_{suffix}" in result.columns
    assert f"watch_qqe_mod_hist_{suffix}" in result.columns
    assert f"watch_qqe_mod_up_{suffix}" in result.columns
    assert f"watch_qqe_mod_down_{suffix}" in result.columns
    assert f"watch_qqe_mod_up_state_{suffix}" in result.columns
    assert f"watch_qqe_mod_down_state_{suffix}" in result.columns
    assert f"watch_qqe_mod_up_event_{suffix}" in result.columns
    assert f"watch_qqe_mod_down_event_{suffix}" in result.columns
    assert result[f"watch_qqe_mod_hist_{suffix}"].notna().sum() > 0


def test_build_watch_plot_config_accepts_custom_qqe_mod_periods():
    indicators = ChartIndicatorRequest(
        ma=[],
        rsi=[],
        macd=[],
        supertrend=[],
        qqe_mod=[QqeModIndicatorRequest(rsi_length=7, threshold=4.0)],
    )

    plot_config = build_watch_plot_config(indicators)

    suffix = "7_5_3_50_0_35_6_5_1_61_4_close"
    assert plot_config["main_plot"] == {}
    assert set(plot_config["subplots"]) == {"QQE MOD"}
    assert plot_config["subplots"]["QQE MOD"] == {
        f"watch_qqe_mod_hist_{suffix}": {"type": "bar", "color": "#64748b"},
        f"watch_qqe_mod_up_{suffix}": {"type": "bar", "color": "#22c55e"},
        f"watch_qqe_mod_down_{suffix}": {"type": "bar", "color": "#ef4444"},
        f"watch_qqe_mod_trend_{suffix}": {"type": "line", "color": "#eab308"},
    }


def test_add_watch_indicators_sanitizes_special_characters_in_qqe_mod_source_suffix():
    dataframe = generate_test_data("15m", 120, "2024-01-01 00:00:00+00:00")
    dataframe["close price(hlc3)"] = dataframe["close"]
    indicators = ChartIndicatorRequest(
        ma=[],
        rsi=[],
        macd=[],
        supertrend=[],
        qqe_mod=[QqeModIndicatorRequest(rsi_length=7, source="close price(hlc3)")],
    )

    result = add_watch_indicators(dataframe, indicators)

    suffix = "7_5_3_50_0_35_6_5_1_61_3_close_price_hlc3_"
    qqe_columns = [column for column in result.columns if column.startswith("watch_qqe_mod_")]
    assert f"watch_qqe_mod_hist_{suffix}" in qqe_columns
    assert all(" " not in column for column in qqe_columns)
    assert all("(" not in column for column in qqe_columns)
    assert all(")" not in column for column in qqe_columns)


def test_add_watch_indicators_preserves_case_in_qqe_mod_source_suffix():
    dataframe = generate_test_data("15m", 120, "2024-01-01 00:00:00+00:00")
    dataframe["foo"] = dataframe["close"]
    dataframe["FOO"] = dataframe["close"] + 1.0
    indicators = ChartIndicatorRequest(
        ma=[],
        rsi=[],
        macd=[],
        supertrend=[],
        qqe_mod=[
            QqeModIndicatorRequest(rsi_length=7, source="foo"),
            QqeModIndicatorRequest(rsi_length=7, source="FOO"),
        ],
    )

    result = add_watch_indicators(dataframe, indicators)

    lower_suffix = "7_5_3_50_0_35_6_5_1_61_3_foo"
    upper_suffix = "7_5_3_50_0_35_6_5_1_61_3_FOO"
    assert f"watch_qqe_mod_hist_{lower_suffix}" in result.columns
    assert f"watch_qqe_mod_hist_{upper_suffix}" in result.columns
    assert len(result.columns) == len(set(result.columns))


def test_add_watch_indicators_rejects_colliding_sanitized_qqe_mod_source_suffixes():
    dataframe = generate_test_data("15m", 120, "2024-01-01 00:00:00+00:00")
    dataframe["a-b"] = dataframe["close"]
    dataframe["a b"] = dataframe["close"] + 1.0
    indicators = ChartIndicatorRequest(
        ma=[],
        rsi=[],
        macd=[],
        supertrend=[],
        qqe_mod=[
            QqeModIndicatorRequest(rsi_length=7, source="a-b"),
            QqeModIndicatorRequest(rsi_length=7, source="a b"),
        ],
    )

    with pytest.raises(ValueError, match="Duplicate QQE MOD watch indicator columns"):
        add_watch_indicators(dataframe, indicators)


def test_build_watch_plot_config_rejects_colliding_sanitized_qqe_mod_source_suffixes():
    indicators = ChartIndicatorRequest(
        ma=[],
        rsi=[],
        macd=[],
        supertrend=[],
        qqe_mod=[
            QqeModIndicatorRequest(rsi_length=7, source="a-b"),
            QqeModIndicatorRequest(rsi_length=7, source="a b"),
        ],
    )

    with pytest.raises(ValueError, match="Duplicate QQE MOD watch indicator columns"):
        build_watch_plot_config(indicators)


def test_add_watch_indicators_accepts_empty_request():
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00")
    indicators = ChartIndicatorRequest(ma=[], rsi=[], macd=[], supertrend=[], qqe_mod=[])

    result = add_watch_indicators(dataframe, indicators)

    assert list(result.columns) == ["date", "open", "high", "low", "close", "volume"]


def test_invalid_macd_period_order_fails_schema():
    with pytest.raises(ValueError, match="slow period"):
        MacdIndicatorRequest(fast=26, slow=12, signal=9)


def test_chart_indicator_request_includes_default_supertrend():
    indicators = ChartIndicatorRequest()

    assert len(indicators.supertrend) == 1
    assert indicators.supertrend[0].period == 10
    assert indicators.supertrend[0].multiplier == pytest.approx(3.0)


def test_chart_indicator_request_accepts_empty_supertrend():
    indicators = ChartIndicatorRequest(supertrend=[])

    assert indicators.supertrend == []


def test_invalid_supertrend_period_fails_schema():
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        SupertrendIndicatorRequest(period=0, multiplier=3)


def test_invalid_supertrend_multiplier_fails_schema():
    with pytest.raises(ValueError, match="greater than 0"):
        SupertrendIndicatorRequest(period=10, multiplier=0)


def test_chart_indicator_request_includes_default_qqe_mod():
    indicators = ChartIndicatorRequest()

    assert len(indicators.qqe_mod) == 1
    assert indicators.qqe_mod[0].rsi_length == 6
    assert indicators.qqe_mod[0].rsi_smoothing == 5
    assert indicators.qqe_mod[0].qqe_factor == pytest.approx(3.0)
    assert indicators.qqe_mod[0].secondary_rsi_length == 6
    assert indicators.qqe_mod[0].secondary_rsi_smoothing == 5
    assert indicators.qqe_mod[0].bollinger_length == 50
    assert indicators.qqe_mod[0].bollinger_multiplier == pytest.approx(0.35)
    assert indicators.qqe_mod[0].secondary_qqe_factor == pytest.approx(1.61)
    assert indicators.qqe_mod[0].threshold == pytest.approx(3.0)
    assert indicators.qqe_mod[0].source == "close"


def test_chart_indicator_request_accepts_empty_qqe_mod():
    indicators = ChartIndicatorRequest(qqe_mod=[])

    assert indicators.qqe_mod == []


def test_invalid_qqe_mod_period_fails_schema():
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        QqeModIndicatorRequest(rsi_length=0)


def test_invalid_qqe_mod_factor_fails_schema():
    with pytest.raises(ValueError, match="greater than 0"):
        QqeModIndicatorRequest(qqe_factor=0)


def test_invalid_qqe_mod_threshold_fails_schema():
    with pytest.raises(ValueError, match="greater than 0"):
        QqeModIndicatorRequest(threshold=0)
