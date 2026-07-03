from __future__ import annotations

from copy import deepcopy
from typing import Any

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.rpc.api_server.api_schemas import ChartIndicatorRequest, MacdIndicatorRequest


DEFAULT_MA_PERIODS = (20, 60)
DEFAULT_RSI_PERIODS = (14,)
DEFAULT_MACD_PERIOD = (12, 26, 9)

DEFAULT_WATCH_PLOT_CONFIG: dict[str, Any] = {
    "main_plot": {
        "watch_ma20": {"color": "#3b82f6"},
        "watch_ma60": {"color": "#f59e0b"},
    },
    "subplots": {
        "RSI 14": {
            "watch_rsi14": {"color": "#a855f7"},
        },
        "MACD": {
            "watch_macd": {"color": "#2563eb"},
            "watch_macdsignal": {"color": "#f97316"},
            "watch_macdhist": {"type": "bar", "color": "#22c55e"},
        },
    },
}


def get_default_watch_indicators() -> ChartIndicatorRequest:
    return ChartIndicatorRequest()


def add_watch_indicators(
    dataframe: DataFrame, indicators: ChartIndicatorRequest | None = None
) -> DataFrame:
    if indicators is None:
        indicators = get_default_watch_indicators()

    result = dataframe.copy()

    for period in indicators.ma:
        result[f"watch_ma{period}"] = ta.SMA(result, timeperiod=period)

    for period in indicators.rsi:
        result[f"watch_rsi{period}"] = ta.RSI(result, timeperiod=period)

    for macd_config in indicators.macd:
        macd = ta.MACD(
            result,
            fastperiod=macd_config.fast,
            slowperiod=macd_config.slow,
            signalperiod=macd_config.signal,
        )
        macd_column, signal_column, hist_column = _macd_column_names(macd_config)
        result[macd_column] = macd["macd"]
        result[signal_column] = macd["macdsignal"]
        result[hist_column] = macd["macdhist"]

    return result


def build_watch_plot_config(indicators: ChartIndicatorRequest | None = None) -> dict[str, Any]:
    if indicators is None or _is_default_watch_indicators(indicators):
        return deepcopy(DEFAULT_WATCH_PLOT_CONFIG)

    plot_config: dict[str, Any] = {"main_plot": {}, "subplots": {}}

    for period in indicators.ma:
        plot_config["main_plot"][f"watch_ma{period}"] = {}

    for period in indicators.rsi:
        plot_config["subplots"][f"RSI {period}"] = {f"watch_rsi{period}": {}}

    if indicators.macd:
        plot_config["subplots"]["MACD"] = {}
        for macd_config in indicators.macd:
            macd_column, signal_column, hist_column = _macd_column_names(macd_config)
            plot_config["subplots"]["MACD"][macd_column] = {}
            plot_config["subplots"]["MACD"][signal_column] = {}
            plot_config["subplots"]["MACD"][hist_column] = {"type": "bar"}

    return plot_config


def _macd_column_names(macd_config: MacdIndicatorRequest) -> tuple[str, str, str]:
    if _macd_period(macd_config) == DEFAULT_MACD_PERIOD:
        return "watch_macd", "watch_macdsignal", "watch_macdhist"

    suffix = f"_{macd_config.fast}_{macd_config.slow}_{macd_config.signal}"
    return (
        f"watch_macd{suffix}",
        f"watch_macdsignal{suffix}",
        f"watch_macdhist{suffix}",
    )


def _is_default_watch_indicators(indicators: ChartIndicatorRequest) -> bool:
    macd_periods = tuple(_macd_period(macd_config) for macd_config in indicators.macd)
    return (
        tuple(indicators.ma) == DEFAULT_MA_PERIODS
        and tuple(indicators.rsi) == DEFAULT_RSI_PERIODS
        and macd_periods == (DEFAULT_MACD_PERIOD,)
    )


def _macd_period(macd_config: MacdIndicatorRequest) -> tuple[int, int, int]:
    return macd_config.fast, macd_config.slow, macd_config.signal
