from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.indicators.qqe_mod import add_qqe_mod
from freqtrade.indicators.supertrend import add_supertrend
from freqtrade.rpc.api_server.api_schemas import (
    ChartIndicatorRequest,
    MacdIndicatorRequest,
    QqeModIndicatorRequest,
    SupertrendIndicatorRequest,
)


DEFAULT_MA_PERIODS = (20, 60)
DEFAULT_RSI_PERIODS = (14,)
DEFAULT_MACD_PERIOD = (12, 26, 9)
DEFAULT_SUPERTREND_PERIOD = (10, 3.0)
DEFAULT_QQE_MOD_PERIOD = (6, 5, 3.0, 50, 0.35, 6, 5, 1.61, 3.0, "close")
QQE_MOD_FIELDS = (
    "trend",
    "hist",
    "up",
    "down",
    "up_state",
    "down_state",
    "up_event",
    "down_event",
)

DEFAULT_WATCH_PLOT_CONFIG: dict[str, Any] = {
    "main_plot": {
        "watch_ma20": {"color": "#3b82f6"},
        "watch_ma60": {"color": "#f59e0b"},
        "watch_supertrend_up": {
            "color": "#22c55e",
            "type": "line",
            "fill_to": "watch_supertrend_price",
        },
        "watch_supertrend_down": {
            "color": "#ef4444",
            "type": "line",
            "fill_to": "watch_supertrend_price",
        },
        "watch_supertrend_price": {"type": "line", "hidden": True},
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
        "QQE MOD": {
            "watch_qqe_mod_hist": {"type": "bar", "color": "#64748b"},
            "watch_qqe_mod_up": {"type": "bar", "color": "#22c55e"},
            "watch_qqe_mod_down": {"type": "bar", "color": "#ef4444"},
            "watch_qqe_mod_trend": {"type": "line", "color": "#eab308"},
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

    for index, supertrend_config in enumerate(indicators.supertrend):
        up_column, down_column, price_column = _supertrend_column_names(supertrend_config)
        if _supertrend_period(supertrend_config) == DEFAULT_SUPERTREND_PERIOD:
            result = add_supertrend(
                result,
                period=supertrend_config.period,
                multiplier=supertrend_config.multiplier,
                prefix="watch_supertrend",
            )
            continue

        temp_prefix = f"__watch_supertrend_{index}"
        result = add_supertrend(
            result,
            period=supertrend_config.period,
            multiplier=supertrend_config.multiplier,
            prefix=temp_prefix,
        )
        result = result.rename(
            columns={
                f"{temp_prefix}_up": up_column,
                f"{temp_prefix}_down": down_column,
                f"{temp_prefix}_price": price_column,
            }
        )

    if indicators.qqe_mod:
        _validate_unique_qqe_mod_columns(indicators.qqe_mod)

    for index, qqe_config in enumerate(indicators.qqe_mod):
        columns = _qqe_mod_column_names(qqe_config)
        if _qqe_mod_period(qqe_config) == DEFAULT_QQE_MOD_PERIOD:
            result = add_qqe_mod(
                result,
                rsi_length=qqe_config.rsi_length,
                rsi_smoothing=qqe_config.rsi_smoothing,
                qqe_factor=qqe_config.qqe_factor,
                bollinger_length=qqe_config.bollinger_length,
                bollinger_multiplier=qqe_config.bollinger_multiplier,
                secondary_rsi_length=qqe_config.secondary_rsi_length,
                secondary_rsi_smoothing=qqe_config.secondary_rsi_smoothing,
                secondary_qqe_factor=qqe_config.secondary_qqe_factor,
                threshold=qqe_config.threshold,
                source=qqe_config.source,
                prefix="watch_qqe_mod",
            )
            continue

        temp_prefix = f"__watch_qqe_mod_{index}"
        result = add_qqe_mod(
            result,
            rsi_length=qqe_config.rsi_length,
            rsi_smoothing=qqe_config.rsi_smoothing,
            qqe_factor=qqe_config.qqe_factor,
            bollinger_length=qqe_config.bollinger_length,
            bollinger_multiplier=qqe_config.bollinger_multiplier,
            secondary_rsi_length=qqe_config.secondary_rsi_length,
            secondary_rsi_smoothing=qqe_config.secondary_rsi_smoothing,
            secondary_qqe_factor=qqe_config.secondary_qqe_factor,
            threshold=qqe_config.threshold,
            source=qqe_config.source,
            prefix=temp_prefix,
        )
        result = result.rename(
            columns={
                f"{temp_prefix}_{field}": columns[field]
                for field in QQE_MOD_FIELDS
            }
        )

    return result


def build_watch_plot_config(indicators: ChartIndicatorRequest | None = None) -> dict[str, Any]:
    if indicators is None or _is_default_watch_indicators(indicators):
        return deepcopy(DEFAULT_WATCH_PLOT_CONFIG)

    plot_config: dict[str, Any] = {"main_plot": {}, "subplots": {}}

    for period in indicators.ma:
        plot_config["main_plot"][f"watch_ma{period}"] = {}

    for supertrend_config in indicators.supertrend:
        up_column, down_column, price_column = _supertrend_column_names(supertrend_config)
        plot_config["main_plot"][up_column] = {
            "color": "#22c55e",
            "type": "line",
            "fill_to": price_column,
        }
        plot_config["main_plot"][down_column] = {
            "color": "#ef4444",
            "type": "line",
            "fill_to": price_column,
        }
        plot_config["main_plot"][price_column] = {"type": "line", "hidden": True}

    for period in indicators.rsi:
        plot_config["subplots"][f"RSI {period}"] = {f"watch_rsi{period}": {}}

    if indicators.macd:
        plot_config["subplots"]["MACD"] = {}
        for macd_config in indicators.macd:
            macd_column, signal_column, hist_column = _macd_column_names(macd_config)
            plot_config["subplots"]["MACD"][macd_column] = {}
            plot_config["subplots"]["MACD"][signal_column] = {}
            plot_config["subplots"]["MACD"][hist_column] = {"type": "bar"}

    if indicators.qqe_mod:
        _validate_unique_qqe_mod_columns(indicators.qqe_mod)
        plot_config["subplots"]["QQE MOD"] = {}
        for qqe_config in indicators.qqe_mod:
            columns = _qqe_mod_column_names(qqe_config)
            plot_config["subplots"]["QQE MOD"][columns["hist"]] = {
                "type": "bar",
                "color": "#64748b",
            }
            plot_config["subplots"]["QQE MOD"][columns["up"]] = {
                "type": "bar",
                "color": "#22c55e",
            }
            plot_config["subplots"]["QQE MOD"][columns["down"]] = {
                "type": "bar",
                "color": "#ef4444",
            }
            plot_config["subplots"]["QQE MOD"][columns["trend"]] = {
                "type": "line",
                "color": "#eab308",
            }

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


def _supertrend_column_names(
    supertrend_config: SupertrendIndicatorRequest,
) -> tuple[str, str, str]:
    if _supertrend_period(supertrend_config) == DEFAULT_SUPERTREND_PERIOD:
        return "watch_supertrend_up", "watch_supertrend_down", "watch_supertrend_price"

    suffix = (
        f"_{supertrend_config.period}_"
        f"{_supertrend_multiplier_suffix(supertrend_config.multiplier)}"
    )
    return (
        f"watch_supertrend_up{suffix}",
        f"watch_supertrend_down{suffix}",
        f"watch_supertrend_price{suffix}",
    )


def _qqe_mod_column_names(qqe_config: QqeModIndicatorRequest) -> dict[str, str]:
    if _qqe_mod_period(qqe_config) == DEFAULT_QQE_MOD_PERIOD:
        return {field: f"watch_qqe_mod_{field}" for field in QQE_MOD_FIELDS}

    suffix = (
        f"_{qqe_config.rsi_length}"
        f"_{qqe_config.rsi_smoothing}"
        f"_{_number_suffix(qqe_config.qqe_factor)}"
        f"_{qqe_config.bollinger_length}"
        f"_{_number_suffix(qqe_config.bollinger_multiplier)}"
        f"_{qqe_config.secondary_rsi_length}"
        f"_{qqe_config.secondary_rsi_smoothing}"
        f"_{_number_suffix(qqe_config.secondary_qqe_factor)}"
        f"_{_number_suffix(qqe_config.threshold)}"
        f"_{_source_suffix(qqe_config.source)}"
    )
    return {field: f"watch_qqe_mod_{field}{suffix}" for field in QQE_MOD_FIELDS}


def _validate_unique_qqe_mod_columns(qqe_configs: list[QqeModIndicatorRequest]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()

    for qqe_config in qqe_configs:
        for column in _qqe_mod_column_names(qqe_config).values():
            if column in seen:
                duplicates.add(column)
            seen.add(column)

    if duplicates:
        duplicate_columns = ", ".join(sorted(duplicates))
        raise ValueError(f"Duplicate QQE MOD watch indicator columns: {duplicate_columns}")


def _supertrend_multiplier_suffix(multiplier: float) -> str:
    return _number_suffix(multiplier)


def _is_default_watch_indicators(indicators: ChartIndicatorRequest) -> bool:
    macd_periods = tuple(_macd_period(macd_config) for macd_config in indicators.macd)
    supertrend_periods = tuple(
        _supertrend_period(supertrend_config) for supertrend_config in indicators.supertrend
    )
    qqe_mod_periods = tuple(_qqe_mod_period(qqe_config) for qqe_config in indicators.qqe_mod)
    return (
        tuple(indicators.ma) == DEFAULT_MA_PERIODS
        and tuple(indicators.rsi) == DEFAULT_RSI_PERIODS
        and macd_periods == (DEFAULT_MACD_PERIOD,)
        and supertrend_periods == (DEFAULT_SUPERTREND_PERIOD,)
        and qqe_mod_periods == (DEFAULT_QQE_MOD_PERIOD,)
    )


def _macd_period(macd_config: MacdIndicatorRequest) -> tuple[int, int, int]:
    return macd_config.fast, macd_config.slow, macd_config.signal


def _supertrend_period(supertrend_config: SupertrendIndicatorRequest) -> tuple[int, float]:
    return supertrend_config.period, _normalized_float(supertrend_config.multiplier)


def _qqe_mod_period(
    qqe_config: QqeModIndicatorRequest,
) -> tuple[int, int, float, int, float, int, int, float, float, str]:
    return (
        qqe_config.rsi_length,
        qqe_config.rsi_smoothing,
        _normalized_float(qqe_config.qqe_factor),
        qqe_config.bollinger_length,
        _normalized_float(qqe_config.bollinger_multiplier),
        qqe_config.secondary_rsi_length,
        qqe_config.secondary_rsi_smoothing,
        _normalized_float(qqe_config.secondary_qqe_factor),
        _normalized_float(qqe_config.threshold),
        qqe_config.source,
    )


def _normalized_float(value: float) -> float:
    normalized = float(value)
    if math.isclose(normalized, round(normalized)):
        normalized = float(round(normalized))
    return normalized


def _number_suffix(value: float) -> str:
    return f"{_normalized_float(value):g}".replace(".", "_")


def _source_suffix(source: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in source)
