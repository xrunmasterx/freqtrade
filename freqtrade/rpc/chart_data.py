from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

import pandas as pd
from pandas import DataFrame, merge_asof

from freqtrade.constants import DEFAULT_DATAFRAME_COLUMNS
from freqtrade.enums import CandleType
from freqtrade.exchange import date_minus_candles, timeframe_to_msecs
from freqtrade.rpc import RPC
from freqtrade.rpc.api_server.api_schemas import ChartCandlesRequest, ChartOverlayMeta
from freqtrade.rpc.chart_indicators import add_watch_indicators, build_watch_plot_config
from freqtrade.util.datetime_helpers import dt_now, dt_ts


logger = logging.getLogger(__name__)

CHART_WARMUP_CANDLES = 120
SIGNAL_COLUMNS = ["enter_long", "exit_long", "enter_short", "exit_short"]
STRATEGY_OVERLAY_HIDDEN_WARNING = (
    "Strategy overlay hidden: chart timeframe is higher than strategy timeframe."
)


def load_chart_ohlcv(
    exchange: Any, config: dict[str, Any], pair: str, timeframe: str, limit: int
) -> DataFrame:
    candles_to_request = limit + CHART_WARMUP_CANDLES
    since_ms = dt_ts(date_minus_candles(timeframe, candles_to_request, dt_now()))

    dataframe = exchange.get_historic_ohlcv(
        pair=pair,
        timeframe=timeframe,
        since_ms=since_ms,
        is_new_pair=True,
        candle_type=config.get("candle_type_def", CandleType.SPOT),
    )
    return dataframe.loc[:, DEFAULT_DATAFRAME_COLUMNS].tail(candles_to_request).reset_index(
        drop=True
    )


def merge_strategy_overlay(
    chart_dataframe: DataFrame,
    strategy_dataframe: DataFrame,
    chart_timeframe: str,
    strategy_timeframe: str,
    strategy_plot_config: dict[str, Any],
) -> tuple[DataFrame, ChartOverlayMeta, list[str]]:
    chart_timeframe_ms = timeframe_to_msecs(chart_timeframe)
    strategy_timeframe_ms = timeframe_to_msecs(strategy_timeframe)
    continuous_columns = _strategy_overlay_columns(strategy_dataframe, strategy_plot_config)

    if chart_timeframe_ms > strategy_timeframe_ms:
        result = _ensure_signal_columns(chart_dataframe)
        overlay = ChartOverlayMeta(
            strategy_timeframe=strategy_timeframe,
            alignment="hidden",
            columns=[],
            hidden=True,
            warning=STRATEGY_OVERLAY_HIDDEN_WARNING,
        )
        return result, overlay, [STRATEGY_OVERLAY_HIDDEN_WARNING]

    alignment = "direct" if chart_timeframe_ms == strategy_timeframe_ms else "forward_fill"
    overlay_columns = [
        _strategy_overlay_column_name(strategy_timeframe, column) for column in continuous_columns
    ]
    signal_columns = [column for column in SIGNAL_COLUMNS if column in strategy_dataframe.columns]

    left = chart_dataframe.drop(columns=SIGNAL_COLUMNS, errors="ignore").copy()
    left.loc[:, "__merge_date"] = _date_merge_key(left)
    result = left

    if continuous_columns:
        continuous_right = strategy_dataframe.loc[:, ["date", *continuous_columns]].copy()
        continuous_right.loc[:, "__merge_date"] = _date_merge_key(continuous_right)
        continuous_right = continuous_right.drop(columns=["date"])
        continuous_right = continuous_right.rename(
            columns={
                column: _strategy_overlay_column_name(strategy_timeframe, column)
                for column in continuous_columns
            }
        )
        if chart_timeframe_ms == strategy_timeframe_ms:
            result = left.merge(continuous_right, on="__merge_date", how="left")
        else:
            result = merge_asof(
                left.sort_values("__merge_date").reset_index(drop=True),
                continuous_right.sort_values("__merge_date").reset_index(drop=True),
                on="__merge_date",
                direction="backward",
            )

    if signal_columns:
        signal_right = strategy_dataframe.loc[:, ["date", *signal_columns]].copy()
        signal_right.loc[:, "__merge_date"] = _date_merge_key(signal_right)
        signal_right = signal_right.drop(columns=["date"])
        result = result.merge(signal_right, on="__merge_date", how="left")

    result = result.drop(columns=["__merge_date"])
    result = _ensure_signal_columns(result)
    overlay = ChartOverlayMeta(
        strategy_timeframe=strategy_timeframe,
        alignment=alignment,
        columns=overlay_columns,
        hidden=False,
    )
    return result, overlay, []


def merge_plot_config(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(left)
    result.setdefault("main_plot", {})
    result.setdefault("subplots", {})

    main_plot = right.get("main_plot", {}) if isinstance(right, dict) else {}
    if isinstance(main_plot, dict):
        for column, column_config in main_plot.items():
            result["main_plot"][column] = deepcopy(column_config)

    subplots = right.get("subplots", {}) if isinstance(right, dict) else {}
    if isinstance(subplots, dict):
        for subplot_name, subplot_columns in subplots.items():
            if not isinstance(subplot_columns, dict):
                continue
            result["subplots"].setdefault(subplot_name, {})
            for column, column_config in subplot_columns.items():
                result["subplots"][subplot_name][column] = deepcopy(column_config)

    return result


def build_chart_candles_response(
    rpc: RPC, config: dict[str, Any], payload: ChartCandlesRequest
) -> dict[str, Any]:
    chart_dataframe = load_chart_ohlcv(
        rpc._freqtrade.exchange,
        config,
        payload.pair,
        payload.timeframe,
        payload.limit,
    )
    chart_dataframe = add_watch_indicators(chart_dataframe, payload.watch_indicators)
    plot_config = build_watch_plot_config(payload.watch_indicators)
    watch_dataframe = chart_dataframe.copy()
    watch_plot_config = deepcopy(plot_config)
    strategy_timeframe = config.get("timeframe") if payload.include_strategy_overlay else None
    overlay: ChartOverlayMeta | None = None
    warnings: list[str] = []

    if payload.include_strategy_overlay and strategy_timeframe:
        try:
            strategy_dataframe, _ = rpc._freqtrade.dataprovider.get_analyzed_dataframe(
                payload.pair, strategy_timeframe
            )
            strategy_plot_config = getattr(rpc._freqtrade.strategy, "plot_config", {}) or {}
            overlay_dataframe, overlay_metadata, overlay_warnings = merge_strategy_overlay(
                chart_dataframe,
                strategy_dataframe.copy(),
                chart_timeframe=payload.timeframe,
                strategy_timeframe=strategy_timeframe,
                strategy_plot_config=strategy_plot_config,
            )
            overlay_plot_config = merge_plot_config(
                watch_plot_config,
                _strategy_overlay_plot_config(
                    strategy_plot_config,
                    strategy_timeframe,
                    overlay_metadata.columns,
                ),
            )
            chart_dataframe = overlay_dataframe
            overlay = overlay_metadata
            plot_config = overlay_plot_config
            warnings = overlay_warnings
        except Exception:
            warning = f"Strategy overlay unavailable for {payload.pair} {strategy_timeframe}"
            logger.warning(warning, exc_info=True)
            warnings = [warning]
            chart_dataframe = _ensure_signal_columns(watch_dataframe)
            plot_config = watch_plot_config
            overlay = ChartOverlayMeta(
                strategy_timeframe=strategy_timeframe,
                alignment="unavailable",
                columns=[],
                hidden=True,
                warning=warning,
            )
    else:
        chart_dataframe = _ensure_signal_columns(watch_dataframe)

    chart_dataframe = _trim_to_limit(chart_dataframe, payload.limit)
    response = RPC._convert_dataframe_to_dict(
        config.get("strategy", ""),
        payload.pair,
        payload.timeframe,
        chart_dataframe.copy(),
        dt_now(),
        None,
        [],
    )
    response.update(
        {
            "chart_timeframe": payload.timeframe,
            "strategy_timeframe": strategy_timeframe,
            "overlay": overlay.model_dump() if overlay else None,
            "plot_config": plot_config,
            "warnings": warnings,
        }
    )
    return response


def _strategy_overlay_columns(
    strategy_dataframe: DataFrame, strategy_plot_config: dict[str, Any]
) -> list[str]:
    if not isinstance(strategy_plot_config, dict):
        return []

    dataframe_columns = set(strategy_dataframe.columns)
    excluded_columns = set(DEFAULT_DATAFRAME_COLUMNS + SIGNAL_COLUMNS)
    columns: list[str] = []

    main_plot = strategy_plot_config.get("main_plot", {})
    if isinstance(main_plot, dict):
        for column in main_plot:
            if column in dataframe_columns and column not in excluded_columns:
                columns.append(column)

    subplots = strategy_plot_config.get("subplots", {})
    if isinstance(subplots, dict):
        for subplot_columns in subplots.values():
            if not isinstance(subplot_columns, dict):
                continue
            for column in subplot_columns:
                if column in dataframe_columns and column not in excluded_columns:
                    columns.append(column)

    return list(dict.fromkeys(columns))


def _strategy_overlay_plot_config(
    strategy_plot_config: dict[str, Any], strategy_timeframe: str, overlay_columns: list[str]
) -> dict[str, Any]:
    if not isinstance(strategy_plot_config, dict) or not overlay_columns:
        return {"main_plot": {}, "subplots": {}}

    prefixed_to_original = {
        _strategy_overlay_column_name(strategy_timeframe, column): column
        for column in _unprefixed_strategy_columns(strategy_timeframe, overlay_columns)
    }
    result: dict[str, Any] = {"main_plot": {}, "subplots": {}}

    main_plot = strategy_plot_config.get("main_plot", {})
    for prefixed_column, original_column in prefixed_to_original.items():
        if isinstance(main_plot, dict) and original_column in main_plot:
            result["main_plot"][prefixed_column] = deepcopy(main_plot[original_column])

    subplots = strategy_plot_config.get("subplots", {})
    if isinstance(subplots, dict):
        for subplot_name, subplot_columns in subplots.items():
            if not isinstance(subplot_columns, dict):
                continue
            for prefixed_column, original_column in prefixed_to_original.items():
                if original_column in subplot_columns:
                    result["subplots"].setdefault(subplot_name, {})
                    result["subplots"][subplot_name][prefixed_column] = deepcopy(
                        subplot_columns[original_column]
                    )

    return result


def _unprefixed_strategy_columns(strategy_timeframe: str, overlay_columns: list[str]) -> list[str]:
    prefix = f"strategy_{strategy_timeframe}_"
    return [column.removeprefix(prefix) for column in overlay_columns]


def _strategy_overlay_column_name(strategy_timeframe: str, column: str) -> str:
    return f"strategy_{strategy_timeframe}_{column}"


def _date_merge_key(dataframe: DataFrame) -> pd.Series:
    return pd.to_datetime(dataframe["date"], utc=True).dt.as_unit("ms").astype("int64")


def _trim_to_limit(dataframe: DataFrame, limit: int) -> DataFrame:
    return dataframe.tail(limit).reset_index(drop=True)


def _ensure_signal_columns(dataframe: DataFrame) -> DataFrame:
    result = dataframe.copy()
    for column in SIGNAL_COLUMNS:
        if column not in result.columns:
            result[column] = 0
        else:
            result[column] = result[column].fillna(0)
    return result
