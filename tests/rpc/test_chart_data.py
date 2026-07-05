import logging
from unittest.mock import MagicMock

import pandas as pd
import pytest

from freqtrade.enums import CandleType
from freqtrade.exchange import date_minus_candles
from freqtrade.rpc.api_server.api_schemas import ChartCandlesRequest
from freqtrade.rpc.chart_data import (
    CHART_WARMUP_CANDLES,
    build_chart_candles_response,
    build_chart_composition,
    clear_chart_ohlcv_cache,
    load_chart_ohlcv,
    merge_strategy_overlay,
)
from tests.conftest import generate_test_data


def _response_column(response, column):
    column_index = response["columns"].index(column)
    return [row[column_index] for row in response["data"]]


def _meta_layer(response, source):
    return next(layer for layer in response["meta"]["layers"] if layer["source"] == source)


def _meta_series_by_column(layer):
    return {series["column"]: series for series in layer["series"]}


def test_load_chart_ohlcv_uses_limit_and_warmup(mocker):
    exchange = MagicMock()
    exchange.get_historic_ohlcv.return_value = generate_test_data(
        "15m", 700, "2024-01-01 00:00:00+00:00"
    )
    config = {"candle_type_def": CandleType.SPOT}
    now = pd.Timestamp("2024-01-08 00:07:30+00:00").to_pydatetime()
    mocker.patch("freqtrade.rpc.chart_data.dt_now", return_value=now)

    result = load_chart_ohlcv(exchange, config, "BTC/USDT", "15m", 500)

    assert len(result) == 620
    assert list(result.columns) == ["date", "open", "high", "low", "close", "volume"]
    exchange.get_historic_ohlcv.assert_called_once()
    _, kwargs = exchange.get_historic_ohlcv.call_args
    assert kwargs["pair"] == "BTC/USDT"
    assert kwargs["timeframe"] == "15m"
    assert kwargs["since_ms"] == int(date_minus_candles("15m", 620, now).timestamp() * 1000)
    assert kwargs["candle_type"] == CandleType.SPOT
    assert kwargs["is_new_pair"] is True


def test_load_chart_ohlcv_live_keeps_incomplete_candle(mocker):
    clear_chart_ohlcv_cache()
    exchange = MagicMock()
    live_df = generate_test_data("1m", 3, "2024-01-01 00:00:00+00:00")
    exchange.refresh_latest_ohlcv.return_value = {
        ("BTC/USDT", "1m", CandleType.SPOT): live_df
    }
    config = {"candle_type_def": CandleType.SPOT}
    now = pd.Timestamp("2024-01-01 00:02:30+00:00").to_pydatetime()
    mocker.patch("freqtrade.rpc.chart_data.dt_now", return_value=now)

    result = load_chart_ohlcv(exchange, config, "BTC/USDT", "1m", 2, candle_mode="live")

    assert result["date"].iloc[-1] == live_df["date"].iloc[-1]
    exchange.refresh_latest_ohlcv.assert_called_once()
    _, kwargs = exchange.refresh_latest_ohlcv.call_args
    assert kwargs["drop_incomplete"] is False
    assert kwargs["cache"] is False


def test_load_chart_ohlcv_live_uses_short_cache(mocker):
    clear_chart_ohlcv_cache()
    exchange = MagicMock()
    live_df = generate_test_data("1m", 3, "2024-01-01 00:00:00+00:00")
    exchange.refresh_latest_ohlcv.return_value = {
        ("BTC/USDT", "1m", CandleType.SPOT): live_df
    }
    config = {"candle_type_def": CandleType.SPOT}
    now = pd.Timestamp("2024-01-01 00:02:30+00:00").to_pydatetime()
    mocker.patch("freqtrade.rpc.chart_data.dt_now", return_value=now)

    load_chart_ohlcv(exchange, config, "BTC/USDT", "1m", 2, candle_mode="live")
    load_chart_ohlcv(exchange, config, "BTC/USDT", "1m", 2, candle_mode="live")

    exchange.refresh_latest_ohlcv.assert_called_once()


def test_merge_strategy_overlay_forward_fills_lower_chart_timeframe():
    chart_df = generate_test_data("15m", 8, "2024-01-01 10:00:00+00:00")
    strategy_df = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-01 10:00:00+00:00", "2024-01-01 11:00:00+00:00"],
                utc=True,
            ),
            "atr": [120.0, 135.0],
            "abs_close_change": [5.0, 8.0],
            "enter_long": [1, 0],
            "exit_long": [0, 0],
            "enter_short": [0, 1],
            "exit_short": [0, 0],
        }
    )

    result, overlay, warnings = merge_strategy_overlay(
        chart_df,
        strategy_df,
        chart_timeframe="15m",
        strategy_timeframe="1h",
        strategy_plot_config={
            "main_plot": {},
            "subplots": {"Volatility system": {"atr": {}, "abs_close_change": {}}},
        },
    )

    assert warnings == []
    assert overlay.hidden is False
    assert overlay.alignment == "forward_fill"
    assert overlay.columns == ["strategy_1h_atr", "strategy_1h_abs_close_change"]
    assert result.loc[0, "strategy_1h_atr"] == 120.0
    assert result.loc[3, "strategy_1h_atr"] == 120.0
    assert result.loc[4, "strategy_1h_atr"] == 135.0
    assert result.loc[0, "enter_long"] == 1
    assert result.loc[4, "enter_short"] == 1


def test_merge_strategy_overlay_does_not_forward_fill_signals_for_lower_chart_timeframe():
    chart_df = generate_test_data("15m", 8, "2024-01-01 10:00:00+00:00")
    strategy_df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01 10:00:00+00:00"], utc=True),
            "atr": [120.0],
            "enter_long": [1],
        }
    )

    result, overlay, warnings = merge_strategy_overlay(
        chart_df,
        strategy_df,
        chart_timeframe="15m",
        strategy_timeframe="1h",
        strategy_plot_config={"main_plot": {"atr": {}}, "subplots": {}},
    )

    assert warnings == []
    assert overlay.alignment == "forward_fill"
    assert result.loc[3, "strategy_1h_atr"] == 120.0
    assert result["enter_long"].sum() == 1
    assert result.loc[0, "enter_long"] == 1
    assert result.loc[1:3, "enter_long"].tolist() == [0, 0, 0]


def test_merge_strategy_overlay_direct_aligns_equal_timeframe():
    chart_df = generate_test_data("1h", 2, "2024-01-01 10:00:00+00:00")
    strategy_df = chart_df[["date"]].copy()
    strategy_df["atr"] = [120.0, 135.0]

    result, overlay, warnings = merge_strategy_overlay(
        chart_df,
        strategy_df,
        chart_timeframe="1h",
        strategy_timeframe="1h",
        strategy_plot_config={"main_plot": {"atr": {}}, "subplots": {}},
    )

    assert warnings == []
    assert overlay.alignment == "direct"
    assert result["strategy_1h_atr"].tolist() == [120.0, 135.0]


def test_merge_strategy_overlay_direct_does_not_backfill_missing_strategy_timestamp():
    chart_df = generate_test_data("1h", 2, "2024-01-01 10:00:00+00:00")
    strategy_df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01 10:00:00+00:00"], utc=True),
            "atr": [120.0],
        }
    )

    result, overlay, warnings = merge_strategy_overlay(
        chart_df,
        strategy_df,
        chart_timeframe="1h",
        strategy_timeframe="1h",
        strategy_plot_config={"main_plot": {"atr": {}}, "subplots": {}},
    )

    assert warnings == []
    assert overlay.alignment == "direct"
    assert result.loc[0, "strategy_1h_atr"] == 120.0
    assert pd.isna(result.loc[1, "strategy_1h_atr"])


def test_merge_strategy_overlay_hides_continuous_overlay_for_higher_chart_timeframe():
    chart_df = generate_test_data("4h", 2, "2024-01-01 00:00:00+00:00")
    strategy_df = generate_test_data("1h", 8, "2024-01-01 00:00:00+00:00")
    strategy_df["atr"] = range(8)

    result, overlay, warnings = merge_strategy_overlay(
        chart_df,
        strategy_df,
        chart_timeframe="4h",
        strategy_timeframe="1h",
        strategy_plot_config={"main_plot": {"atr": {}}, "subplots": {}},
    )

    assert "strategy_1h_atr" not in result.columns
    assert overlay.hidden is True
    assert overlay.alignment == "hidden"
    assert warnings == [
        "Strategy overlay hidden: chart timeframe is higher than strategy timeframe."
    ]


def test_build_chart_candles_response_returns_watch_data_when_overlay_fails(mocker):
    chart_df = generate_test_data("15m", 150, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {"atr": {}}, "subplots": {}}
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.side_effect = RuntimeError("overlay down")
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="15m", limit=50)

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["pair"] == "BTC/USDT"
    assert response["chart_timeframe"] == "15m"
    assert response["strategy_timeframe"] == "1h"
    assert response["length"] == 50
    assert "watch_ma20" in response["columns"]
    assert any("Strategy overlay unavailable" in warning for warning in response["warnings"])


def test_build_chart_candles_response_includes_chart_meta(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["meta"]["schema_version"] == 1
    assert response["meta"]["window"]["requested_count"] == request.limit
    assert response["meta"]["window"]["returned_count"] == len(response["data"])
    assert response["meta"]["window"]["warmup_count"] == CHART_WARMUP_CANDLES
    assert response["meta"]["window"]["data_start"] is not None
    assert response["meta"]["window"]["data_stop"] is not None
    market_layer = _meta_layer(response, "market")
    assert market_layer["source"] == "market"
    assert market_layer["id"] == "market.ohlcv"
    assert {"open", "high", "low", "close", "volume"} <= {
        series["column"] for series in market_layer["series"]
    }
    assert any(layer["source"] == "watch" for layer in response["meta"]["layers"])


@pytest.mark.parametrize("display_count", [20, 250])
def test_build_chart_candles_response_includes_display_count_hint(mocker, display_count):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    baseline_request = ChartCandlesRequest(
        pair="BTC/USDT",
        timeframe="15m",
        limit=50,
        include_strategy_overlay=False,
    )
    request = ChartCandlesRequest(
        pair="BTC/USDT",
        timeframe="15m",
        limit=50,
        display_count=display_count,
        include_strategy_overlay=False,
    )

    baseline_response = build_chart_candles_response(rpc, rpc._freqtrade.config, baseline_request)
    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["meta"]["window"]["requested_count"] == request.limit
    assert response["meta"]["window"]["returned_count"] == len(response["data"])
    assert response["meta"]["window"]["warmup_count"] == CHART_WARMUP_CANDLES
    assert response["meta"]["window"]["display_default_count"] == display_count
    assert len(response["data"]) == len(baseline_response["data"]) == request.limit
    assert response["data"] == baseline_response["data"]


def test_build_chart_candles_response_keeps_legacy_fields_with_meta_layers(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["chart_timeframe"] == "15m"
    assert response["strategy_timeframe"] is None
    assert response["overlay"] is None
    assert response["plot_config"]["main_plot"]["watch_ma20"] == {"color": "#3b82f6"}
    assert response["warnings"] == []
    assert response["candle_mode"] == "closed"
    assert response["last_candle_complete"] is True
    assert response["meta"]["window"]["last_candle_complete"] is True
    assert response["meta"]["layers"]
    assert response["length"] == 50
    assert len(response["columns"]) == len(response["data"][0])


def test_build_chart_composition_returns_frame_layers_and_legacy_meta(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    composition = build_chart_composition(rpc, rpc._freqtrade.config, request)

    assert len(composition.frame.dataframe) == 50
    assert composition.frame.pair == "BTC/USDT"
    assert composition.frame.timeframe == "15m"
    assert composition.frame.requested_count == 50
    assert [layer.source for layer in composition.layers] == ["market", "watch"]
    assert composition.legacy_update()["meta"]["schema_version"] == 1
    assert composition.strategy_timeframe is None
    assert composition.overlay is None
    assert composition.candle_mode == "closed"


def test_build_chart_composition_includes_strategy_overlay_state(mocker):
    chart_df = generate_test_data("15m", 8, "2024-01-01 10:00:00+00:00")
    strategy_df = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-01 10:00:00+00:00", "2024-01-01 11:00:00+00:00"],
                utc=True,
            ),
            "atr": [120.0, 135.0],
            "enter_long": [1, 0],
        }
    )
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {"atr": {"color": "blue"}}, "subplots": {}}
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.return_value = (
        strategy_df,
        pd.Timestamp("2024-01-01 12:00:00+00:00").to_pydatetime(),
    )
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="15m", limit=8)

    composition = build_chart_composition(rpc, rpc._freqtrade.config, request)

    strategy_layer = next(layer for layer in composition.layers if layer.source == "strategy")
    legacy_update = composition.legacy_update()

    assert composition.strategy_timeframe == "1h"
    assert composition.overlay.alignment == "forward_fill"
    assert composition.overlay.hidden is False
    assert "strategy_1h_atr" in composition.frame.dataframe.columns
    assert strategy_layer.dataframe.columns.tolist() == ["date", "strategy_1h_atr"]
    assert "strategy_1h_atr" in strategy_layer.plot_config["main_plot"]
    assert "strategy_1h_atr" in composition.plot_config["main_plot"]
    assert any(layer.source == "strategy" for layer in composition.meta.layers)
    assert legacy_update["plot_config"] == composition.plot_config
    assert legacy_update["warnings"] == []
    assert legacy_update["meta"] == composition.meta.model_dump()
    assert legacy_update["last_candle_complete"] == composition.frame.last_candle_complete


def test_build_chart_candles_response_reports_live_incomplete_candle(mocker):
    clear_chart_ohlcv_cache()
    chart_df = generate_test_data("1m", 150, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.refresh_latest_ohlcv.return_value = {
        ("BTC/USDT", "1m", CandleType.SPOT): chart_df
    }
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    now = pd.Timestamp("2024-01-01 02:29:30+00:00").to_pydatetime()
    mocker.patch("freqtrade.rpc.chart_data.dt_now", return_value=now)
    request = ChartCandlesRequest(
        pair="BTC/USDT",
        timeframe="1m",
        limit=50,
        include_strategy_overlay=False,
        candle_mode="live",
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["candle_mode"] == "live"
    assert response["last_candle_complete"] is False
    assert response["length"] == 50


def test_build_chart_candles_response_live_meta_matches_top_level(mocker):
    clear_chart_ohlcv_cache()
    chart_df = generate_test_data("1m", 150, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.refresh_latest_ohlcv.return_value = {
        ("BTC/USDT", "1m", CandleType.SPOT): chart_df
    }
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    now = pd.Timestamp("2024-01-01 02:29:30+00:00").to_pydatetime()
    mocker.patch("freqtrade.rpc.chart_data.dt_now", return_value=now)
    request = ChartCandlesRequest(
        pair="BTC/USDT",
        timeframe="1m",
        limit=50,
        include_strategy_overlay=False,
        candle_mode="live",
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["meta"]["window"]["last_candle_complete"] == response["last_candle_complete"]


def test_build_chart_candles_response_returns_watch_only_when_overlay_plot_config_fails(
    mocker, caplog
):
    chart_df = generate_test_data("15m", 150, "2024-01-01 00:00:00+00:00")
    strategy_df = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-01 10:00:00+00:00", "2024-01-01 11:00:00+00:00"],
                utc=True,
            ),
            "atr": [120.0, 135.0],
        }
    )
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {"atr": {}}, "subplots": {}}
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.return_value = (
        strategy_df,
        pd.Timestamp("2024-01-01 12:00:00+00:00").to_pydatetime(),
    )
    mocker.patch(
        "freqtrade.rpc.chart_data._strategy_overlay_plot_config",
        side_effect=RuntimeError("plot config down"),
    )
    caplog.set_level(logging.WARNING, logger="freqtrade.rpc.chart_data")
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="15m", limit=50)

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["warnings"] == ["Strategy overlay unavailable for BTC/USDT 1h"]
    assert "watch_ma20" in response["columns"]
    assert not any(column.startswith("strategy_") for column in response["columns"])
    assert response["overlay"]["hidden"] is True
    assert response["overlay"]["alignment"] == "unavailable"
    assert response["overlay"]["columns"] == []
    strategy_layer = _meta_layer(response, "strategy")
    assert strategy_layer["status"] == "unavailable"
    assert strategy_layer["warnings"] == ["Strategy overlay unavailable for BTC/USDT 1h"]
    assert "Strategy overlay unavailable for BTC/USDT 1h" in caplog.text


def test_build_chart_candles_response_includes_strategy_overlay(mocker):
    chart_df = generate_test_data("15m", 8, "2024-01-01 10:00:00+00:00")
    strategy_df = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-01 10:00:00+00:00", "2024-01-01 11:00:00+00:00"],
                utc=True,
            ),
            "atr": [120.0, 135.0],
            "enter_long": [1, 0],
        }
    )
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {"atr": {"color": "blue"}}, "subplots": {}}
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.return_value = (
        strategy_df,
        pd.Timestamp("2024-01-01 12:00:00+00:00").to_pydatetime(),
    )
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="15m", limit=8)

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["strategy_timeframe"] == "1h"
    assert response["overlay"]["alignment"] == "forward_fill"
    assert "strategy_1h_atr" in response["columns"]
    assert "strategy_1h_atr" in response["plot_config"]["main_plot"]
    assert response["warnings"] == []


def test_chart_meta_separates_watch_and_strategy_sources(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    strategy_df = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-02 12:00:00+00:00", "2024-01-02 13:00:00+00:00"],
                utc=True,
            ),
            "atr": [120.0, 135.0],
        }
    )
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {"atr": {"color": "blue"}}, "subplots": {}}
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.return_value = (
        strategy_df,
        pd.Timestamp("2024-01-02 14:00:00+00:00").to_pydatetime(),
    )
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="15m", limit=50)

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    watch_layer = _meta_layer(response, "watch")
    strategy_layer = _meta_layer(response, "strategy")
    assert watch_layer["label"] == "Watch Indicators"
    assert strategy_layer["label"] == "Strategy Output"
    assert all(series["source"] == "watch" for series in watch_layer["series"])
    assert all(series["source"] == "strategy" for series in strategy_layer["series"])


def test_chart_meta_marks_partial_strategy_coverage(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    strategy_df = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-02 12:00:00+00:00", "2024-01-02 13:00:00+00:00"],
                utc=True,
            ),
            "atr": [120.0, 135.0],
        }
    )
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {"atr": {"color": "blue"}}, "subplots": {}}
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.return_value = (
        strategy_df,
        pd.Timestamp("2024-01-02 14:00:00+00:00").to_pydatetime(),
    )
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="15m", limit=50)

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    strategy_layer = _meta_layer(response, "strategy")
    assert strategy_layer["status"] == "partial"
    assert any(
        series["coverage"]["reason"] == "partial coverage"
        and series["coverage"]["valid_points"] < series["coverage"]["total_points"]
        for series in strategy_layer["series"]
    )


def test_chart_meta_marks_hidden_strategy_overlay(mocker):
    chart_df = generate_test_data("4h", 50, "2024-01-01 00:00:00+00:00")
    strategy_df = generate_test_data("1h", 200, "2024-01-01 00:00:00+00:00")
    strategy_df["atr"] = range(200)
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {"atr": {"color": "blue"}}, "subplots": {}}
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.return_value = (
        strategy_df,
        pd.Timestamp("2024-01-09 08:00:00+00:00").to_pydatetime(),
    )
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="4h", limit=50)

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    strategy_layer = _meta_layer(response, "strategy")
    assert strategy_layer["status"] == "hidden"
    assert response["warnings"] == [
        "Strategy overlay hidden: chart timeframe is higher than strategy timeframe."
    ]
    assert strategy_layer["warnings"] == [
        "Strategy overlay hidden: chart timeframe is higher than strategy timeframe."
    ]
    assert response["meta"]["warnings"] == [
        "Strategy overlay hidden: chart timeframe is higher than strategy timeframe."
    ]


def test_chart_meta_omits_empty_ok_strategy_layer_when_overlay_has_no_plot_columns(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    strategy_df = chart_df[["date"]].copy()
    strategy_df["unplotted"] = range(len(strategy_df))
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "15m",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {}, "subplots": {}}
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.return_value = (
        strategy_df,
        pd.Timestamp("2024-01-02 18:30:00+00:00").to_pydatetime(),
    )
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="15m", limit=50)

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert not any(layer["source"] == "strategy" for layer in response["meta"]["layers"])


def test_build_chart_candles_response_propagates_ohlcv_failures(mocker):
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.side_effect = RuntimeError("ohlcv down")
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(pair="BTC/USDT", timeframe="15m", limit=50)

    with pytest.raises(RuntimeError, match="ohlcv down"):
        build_chart_candles_response(rpc, rpc._freqtrade.config, request)


def test_build_chart_candles_response_skips_strategy_overlay_when_excluded(mocker):
    chart_df = generate_test_data("15m", 150, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    rpc._freqtrade.strategy.plot_config = {"main_plot": {"atr": {}}, "subplots": {}}
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["overlay"] is None
    assert response["strategy_timeframe"] is None
    assert not any(column.startswith("strategy_") for column in response["columns"])
    rpc._freqtrade.dataprovider.get_analyzed_dataframe.assert_not_called()


def test_build_chart_candles_response_includes_watch_plot_config(mocker):
    chart_df = generate_test_data("15m", 150, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert "watch_ma20" in response["plot_config"]["main_plot"]
    assert "watch_ma60" in response["plot_config"]["main_plot"]
    assert "watch_supertrend_up" in response["plot_config"]["main_plot"]
    assert "watch_supertrend_down" in response["plot_config"]["main_plot"]
    assert response["plot_config"]["main_plot"]["watch_supertrend_price"]["hidden"] is True
    assert "watch_rsi14" in response["plot_config"]["subplots"]["RSI 14"]
    assert "QQE MOD" in response["plot_config"]["subplots"]
    assert "watch_qqe_mod_hist" in response["plot_config"]["subplots"]["QQE MOD"]
    assert "watch_qqe_mod_trend" in response["plot_config"]["subplots"]["QQE MOD"]


def test_chart_meta_labels_macd_watch_columns_as_macd(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    watch_series = _meta_series_by_column(_meta_layer(response, "watch"))
    assert watch_series["watch_macd"]["label"] == "MACD - Watch"
    assert watch_series["watch_macdsignal"]["label"] == "MACDSIGNAL - Watch"
    assert watch_series["watch_macdhist"]["label"] == "MACDHIST - Watch"
    assert watch_series["watch_macd"]["label"] != "MA(cd) - Watch"
    assert watch_series["watch_macdsignal"]["label"] != "MA(cdsignal) - Watch"
    assert watch_series["watch_macdhist"]["label"] != "MA(cdhist) - Watch"


def test_build_chart_candles_response_includes_qqe_mod_watch_indicator(mocker):
    chart_df = generate_test_data("15m", 260, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert "watch_qqe_mod_hist" in response["columns"]
    assert "watch_qqe_mod_trend" in response["columns"]
    assert "watch_qqe_mod_up" in response["columns"]
    assert "watch_qqe_mod_down" in response["columns"]
    assert "QQE MOD" in response["plot_config"]["subplots"]
    assert any(value is not None for value in _response_column(response, "watch_qqe_mod_hist"))


def test_build_chart_candles_response_includes_supertrend_watch_indicator(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert "watch_supertrend_up" in response["columns"]
    assert "watch_supertrend_down" in response["columns"]
    assert "watch_supertrend_price" in response["columns"]
    assert "watch_supertrend_up" in response["plot_config"]["main_plot"]
    assert "watch_supertrend_down" in response["plot_config"]["main_plot"]
    assert response["plot_config"]["main_plot"]["watch_supertrend_price"]["hidden"] is True
    populated = [
        row
        for row in response["data"]
        if row[response["columns"].index("watch_supertrend_up")] is not None
        or row[response["columns"].index("watch_supertrend_down")] is not None
    ]
    assert len(populated) > 0


def test_build_chart_candles_response_keeps_warmup_for_watch_indicators(mocker):
    chart_df = generate_test_data("15m", 170, "2024-01-01 00:00:00+00:00")
    rpc = MagicMock()
    rpc._freqtrade.exchange.get_historic_ohlcv.return_value = chart_df
    rpc._freqtrade.config = {
        "strategy": "StrategyUnderTest",
        "timeframe": "1h",
        "candle_type_def": CandleType.SPOT,
    }
    request = ChartCandlesRequest(
        pair="BTC/USDT", timeframe="15m", limit=50, include_strategy_overlay=False
    )

    response = build_chart_candles_response(rpc, rpc._freqtrade.config, request)

    assert response["length"] == 50
    assert "watch_ma60" in response["columns"]
    assert all(value is not None for value in _response_column(response, "watch_ma60"))
    assert "watch_supertrend_up" in response["columns"]
    assert "watch_supertrend_down" in response["columns"]
    assert any(
        value is not None
        for value in _response_column(response, "watch_supertrend_up")
        + _response_column(response, "watch_supertrend_down")
    )
    assert "watch_qqe_mod_hist" in response["columns"]
    assert any(value is not None for value in _response_column(response, "watch_qqe_mod_hist"))
