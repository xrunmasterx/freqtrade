from __future__ import annotations

from typing import Any

import pandas as pd
from pandas import DataFrame

from freqtrade.research import LocalCsvResearchDataSource, ResearchBotProfile
from freqtrade.rpc import RPC
from freqtrade.rpc.api_server.api_schemas import (
    ChartResponseMeta,
    ChartWindowMeta,
    ResearchChartCandlesRequest,
)
from freqtrade.rpc.chart_data import _build_market_layer_meta, _build_watch_layer_meta
from freqtrade.rpc.chart_indicators import add_watch_indicators, build_watch_plot_config
from freqtrade.util.datetime_helpers import dt_now


def build_research_chart_candles_response(
    profile: ResearchBotProfile,
    payload: ResearchChartCandlesRequest,
) -> dict[str, Any]:
    dataframe = LocalCsvResearchDataSource(profile.data_root).load_ohlcv(
        payload.instrument,
        payload.timeframe,
    )
    dataframe = dataframe.tail(payload.limit).reset_index(drop=True)
    dataframe = add_watch_indicators(dataframe, payload.watch_indicators)
    plot_config = build_watch_plot_config(payload.watch_indicators)
    meta = _build_research_chart_response_meta(dataframe, payload, plot_config)

    response = RPC._convert_dataframe_to_dict(
        "",
        payload.instrument,
        payload.timeframe,
        dataframe.copy(),
        dt_now(),
        None,
        [],
    )
    response.update(
        {
            "chart_timeframe": payload.timeframe,
            "strategy_timeframe": None,
            "overlay": None,
            "plot_config": plot_config,
            "warnings": meta.warnings,
            "candle_mode": "closed",
            "last_candle_complete": True,
            "meta": meta.model_dump(),
        }
    )
    return response


def _build_research_chart_response_meta(
    dataframe: DataFrame,
    payload: ResearchChartCandlesRequest,
    plot_config: dict[str, Any],
) -> ChartResponseMeta:
    layers = [
        _build_market_layer_meta(dataframe, payload.timeframe),
        _build_watch_layer_meta(dataframe, plot_config, payload.timeframe),
    ]
    warnings = []
    for layer in layers:
        warnings.extend(layer.warnings)

    return ChartResponseMeta(
        window=ChartWindowMeta(
            requested_count=payload.limit,
            returned_count=len(dataframe),
            warmup_count=0,
            data_start=_date_string(dataframe.iloc[0]["date"]) if not dataframe.empty else None,
            data_stop=_date_string(dataframe.iloc[-1]["date"]) if not dataframe.empty else None,
            last_candle_complete=True,
        ),
        layers=layers,
        warnings=list(dict.fromkeys(warnings)),
    )


def _date_string(value: Any) -> str:
    return str(pd.to_datetime(value, utc=True))
