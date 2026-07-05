import pandas as pd

from freqtrade.rpc.api_server.api_schemas import (
    ChartLayerMeta,
    ChartOverlayMeta,
    ChartResponseMeta,
    ChartSeriesCoverage,
    ChartSeriesMeta,
    ChartWindowMeta,
)
from freqtrade.rpc.chart_composition import ChartComposition, ChartFrame, ChartLayer


def _dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-01 00:00:00+00:00", "2024-01-01 00:01:00+00:00"],
                utc=True,
            ),
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.0, 2.0],
            "volume": [10.0, 20.0],
            "watch_rsi14": [None, 55.0],
        }
    )


def _meta(dataframe: pd.DataFrame) -> ChartResponseMeta:
    return ChartResponseMeta(
        window=ChartWindowMeta(
            requested_count=2,
            returned_count=len(dataframe),
            warmup_count=120,
            last_candle_complete=True,
        ),
        layers=[
            ChartLayerMeta(
                id="watch.indicators",
                source="watch",
                status="partial",
                label="Watch Indicators",
                timeframe="1m",
                alignment="direct",
                series=[
                    ChartSeriesMeta(
                        column="watch_rsi14",
                        label="RSI(14) - Watch",
                        source="watch",
                        kind="line",
                        panel="RSI 14",
                        timeframe="1m",
                        coverage=ChartSeriesCoverage(valid_points=1, total_points=len(dataframe)),
                    )
                ],
            )
        ],
    )


def test_chart_composition_keeps_frame_and_layers_separate():
    dataframe = _dataframe()
    frame = ChartFrame(
        dataframe=dataframe,
        pair="BTC/USDT",
        timeframe="1m",
        requested_count=2,
        warmup_count=120,
        last_candle_complete=True,
    )
    market_layer = ChartLayer(
        id="market.ohlcv",
        source="market",
        label="Market Data",
        dataframe=dataframe[["date", "open", "high", "low", "close", "volume"]],
    )
    watch_layer = ChartLayer(
        id="watch.indicators",
        source="watch",
        label="Watch Indicators",
        dataframe=dataframe[["date", "watch_rsi14"]],
    )

    composition = ChartComposition(
        frame=frame,
        layers=[market_layer, watch_layer],
        strategy_timeframe="1h",
        overlay=ChartOverlayMeta(
            strategy_timeframe="1h",
            alignment="forward_fill",
            columns=["strategy_1h_atr"],
        ),
        candle_mode="live",
        plot_config={"main_plot": {}, "subplots": {"RSI 14": {"watch_rsi14": {}}}},
    )

    assert composition.frame.timeframe == "1m"
    assert composition.frame.dataframe is dataframe
    assert composition.layers[0].source == "market"
    assert composition.layers[1].dataframe.columns.tolist() == ["date", "watch_rsi14"]
    assert composition.strategy_timeframe == "1h"
    assert composition.overlay.strategy_timeframe == "1h"
    assert composition.candle_mode == "live"


def test_chart_composition_to_legacy_update_contains_meta():
    dataframe = _dataframe()
    composition = ChartComposition(
        frame=ChartFrame(
            dataframe=dataframe,
            pair="BTC/USDT",
            timeframe="1m",
            requested_count=2,
            warmup_count=120,
            last_candle_complete=False,
        ),
        layers=[],
        strategy_timeframe=None,
        overlay=None,
        candle_mode="live",
        plot_config={"main_plot": {"watch_rsi14": {}}, "subplots": {}},
        warnings=["partial coverage"],
        meta=_meta(dataframe),
    )

    legacy_update = composition.legacy_update()

    assert legacy_update["plot_config"] == {"main_plot": {"watch_rsi14": {}}, "subplots": {}}
    assert legacy_update["warnings"] == ["partial coverage"]
    assert legacy_update["last_candle_complete"] is False
    assert legacy_update["meta"]["schema_version"] == 1


def test_chart_composition_coverage_counts_valid_values_after_trim():
    dataframe = _dataframe()
    composition = ChartComposition(
        frame=ChartFrame(
            dataframe=dataframe,
            pair="BTC/USDT",
            timeframe="1m",
            requested_count=2,
            warmup_count=120,
            last_candle_complete=True,
        ),
        layers=[
            ChartLayer(
                id="watch.indicators",
                source="watch",
                label="Watch Indicators",
                dataframe=dataframe[["date", "watch_rsi14"]],
                meta=_meta(dataframe).layers[0],
            )
        ],
        strategy_timeframe=None,
        overlay=None,
        candle_mode="closed",
        plot_config={"main_plot": {}, "subplots": {"RSI 14": {"watch_rsi14": {}}}},
        meta=_meta(dataframe),
    )

    legacy_update = composition.legacy_update()
    watch_series = legacy_update["meta"]["layers"][0]["series"][0]

    assert legacy_update["meta"]["window"]["returned_count"] == 2
    assert watch_series["coverage"]["valid_points"] == 1
    assert watch_series["coverage"]["total_points"] == 2
