from __future__ import annotations

import logging
import time
from copy import deepcopy
from typing import Any

import pandas as pd
from pandas import DataFrame, merge_asof

from freqtrade.constants import DEFAULT_DATAFRAME_COLUMNS
from freqtrade.enums import CandleType
from freqtrade.exchange import date_minus_candles, timeframe_to_msecs
from freqtrade.rpc import RPC
from freqtrade.rpc.api_server.api_schemas import (
    ChartCandlesRequest,
    ChartLayerMeta,
    ChartOverlayMeta,
    ChartResponseMeta,
    ChartSeriesCoverage,
    ChartSeriesMeta,
    ChartWindowMeta,
)
from freqtrade.rpc.chart_composition import ChartComposition, ChartFrame, ChartLayer
from freqtrade.rpc.chart_indicators import add_watch_indicators, build_watch_plot_config
from freqtrade.util.datetime_helpers import dt_now, dt_ts


logger = logging.getLogger(__name__)

CHART_WARMUP_CANDLES = 120
CHART_OHLCV_CACHE_TTL_SECONDS = 5
SIGNAL_COLUMNS = ["enter_long", "exit_long", "enter_short", "exit_short"]
STRATEGY_OVERLAY_HIDDEN_WARNING = (
    "Strategy overlay hidden: chart timeframe is higher than strategy timeframe."
)
_chart_ohlcv_cache: dict[tuple[int, str, str, CandleType, int, str], tuple[float, DataFrame]] = {}


def clear_chart_ohlcv_cache() -> None:
    _chart_ohlcv_cache.clear()


def load_chart_ohlcv(
    exchange: Any,
    config: dict[str, Any],
    pair: str,
    timeframe: str,
    limit: int,
    candle_mode: str = "closed",
) -> DataFrame:
    candles_to_request = limit + CHART_WARMUP_CANDLES
    since_ms = dt_ts(date_minus_candles(timeframe, candles_to_request, dt_now()))
    candle_type = config.get("candle_type_def", CandleType.SPOT)

    if candle_mode == "live":
        return _load_live_chart_ohlcv(
            exchange,
            pair,
            timeframe,
            candles_to_request,
            since_ms,
            candle_type,
        )

    dataframe = exchange.get_historic_ohlcv(
        pair=pair,
        timeframe=timeframe,
        since_ms=since_ms,
        is_new_pair=True,
        candle_type=candle_type,
    )
    return dataframe.loc[:, DEFAULT_DATAFRAME_COLUMNS].tail(candles_to_request).reset_index(
        drop=True
    )


def _load_live_chart_ohlcv(
    exchange: Any,
    pair: str,
    timeframe: str,
    candles_to_request: int,
    since_ms: int,
    candle_type: CandleType,
) -> DataFrame:
    cache_key = (id(exchange), pair, timeframe, candle_type, candles_to_request, "live")
    cached = _chart_ohlcv_cache.get(cache_key)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1].copy()

    pair_key = (pair, timeframe, candle_type)
    dataframes = exchange.refresh_latest_ohlcv(
        [pair_key],
        since_ms=since_ms,
        cache=False,
        drop_incomplete=False,
    )
    if pair_key not in dataframes:
        raise RuntimeError(f"No OHLCV data returned for {pair} {timeframe} {candle_type}")

    dataframe = dataframes[pair_key]
    result = dataframe.loc[:, DEFAULT_DATAFRAME_COLUMNS].tail(candles_to_request).reset_index(
        drop=True
    )
    _chart_ohlcv_cache[cache_key] = (
        now + CHART_OHLCV_CACHE_TTL_SECONDS,
        result.copy(),
    )
    return result


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
    composition = build_chart_composition(rpc, config, payload)
    response = RPC._convert_dataframe_to_dict(
        config.get("strategy", ""),
        composition.frame.pair,
        composition.frame.timeframe,
        composition.frame.dataframe.copy(),
        dt_now(),
        None,
        [],
    )
    response.update(
        {
            "chart_timeframe": composition.frame.timeframe,
            "strategy_timeframe": composition.strategy_timeframe,
            "overlay": composition.overlay.model_dump() if composition.overlay else None,
            "candle_mode": composition.candle_mode,
        }
    )
    response.update(composition.legacy_update())
    return response


def build_chart_composition(
    rpc: RPC, config: dict[str, Any], payload: ChartCandlesRequest
) -> ChartComposition:
    chart_dataframe = load_chart_ohlcv(
        rpc._freqtrade.exchange,
        config,
        payload.pair,
        payload.timeframe,
        payload.limit,
        payload.candle_mode,
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
    last_candle_complete = payload.candle_mode == "closed" or _last_candle_complete(
        chart_dataframe, payload.timeframe
    )
    meta = _build_chart_response_meta(
        chart_dataframe,
        payload,
        plot_config,
        config.get("strategy", ""),
        strategy_timeframe,
        overlay,
        warnings,
        last_candle_complete,
    )
    frame = ChartFrame(
        dataframe=chart_dataframe,
        pair=payload.pair,
        timeframe=payload.timeframe,
        requested_count=payload.limit,
        warmup_count=CHART_WARMUP_CANDLES,
        last_candle_complete=last_candle_complete,
    )
    composition = ChartComposition(
        frame=frame,
        layers=_build_composition_layers(chart_dataframe, plot_config, meta),
        strategy_timeframe=strategy_timeframe,
        overlay=overlay,
        candle_mode=payload.candle_mode,
        plot_config=plot_config,
        warnings=warnings,
        meta=meta,
    )
    return composition


def _build_chart_response_meta(
    dataframe: DataFrame,
    payload: ChartCandlesRequest,
    plot_config: dict[str, Any],
    strategy_name: str,
    strategy_timeframe: str | None,
    overlay: ChartOverlayMeta | None,
    warnings: list[str],
    last_candle_complete: bool,
) -> ChartResponseMeta:
    layers = [
        _build_market_layer_meta(dataframe, payload.timeframe),
        _build_watch_layer_meta(dataframe, plot_config, payload.timeframe),
    ]
    strategy_layer = _build_strategy_layer_meta(
        dataframe,
        plot_config,
        strategy_name,
        strategy_timeframe,
        overlay,
    )
    if strategy_layer:
        layers.append(strategy_layer)

    meta_warnings = list(warnings)
    for layer in layers:
        meta_warnings.extend(layer.warnings)

    return ChartResponseMeta(
        window=ChartWindowMeta(
            requested_count=payload.limit,
            returned_count=len(dataframe),
            warmup_count=CHART_WARMUP_CANDLES,
            display_default_count=payload.display_count,
            data_start=_date_string(dataframe.iloc[0]["date"]) if not dataframe.empty else None,
            data_stop=_date_string(dataframe.iloc[-1]["date"]) if not dataframe.empty else None,
            last_candle_complete=last_candle_complete,
        ),
        layers=layers,
        warnings=list(dict.fromkeys(meta_warnings)),
    )


def _build_composition_layers(
    dataframe: DataFrame, plot_config: dict[str, Any], meta: ChartResponseMeta
) -> list[ChartLayer]:
    return [
        ChartLayer(
            id=layer_meta.id,
            source=layer_meta.source,
            label=layer_meta.label,
            dataframe=_layer_dataframe(dataframe, layer_meta),
            plot_config=_layer_plot_config(plot_config, layer_meta),
            meta=layer_meta,
        )
        for layer_meta in meta.layers
    ]


def _layer_dataframe(dataframe: DataFrame, layer_meta: ChartLayerMeta) -> DataFrame:
    columns = ["date", *(series.column for series in layer_meta.series)]
    available_columns = [column for column in columns if column in dataframe.columns]
    return dataframe.loc[:, available_columns].copy()


def _layer_plot_config(
    plot_config: dict[str, Any], layer_meta: ChartLayerMeta
) -> dict[str, Any]:
    layer_columns = {series.column for series in layer_meta.series}
    if not layer_columns:
        return {}

    result: dict[str, Any] = {"main_plot": {}, "subplots": {}}
    for panel, column, config in _iter_plot_columns(plot_config):
        if column not in layer_columns:
            continue
        if panel == "main":
            result["main_plot"][column] = deepcopy(config)
        else:
            result["subplots"].setdefault(panel, {})
            result["subplots"][panel][column] = deepcopy(config)
    return result


def _build_market_layer_meta(dataframe: DataFrame, timeframe: str) -> ChartLayerMeta:
    series = [
        _series_meta(dataframe, "open", "Open", "market", "ohlcv", "main", timeframe),
        _series_meta(dataframe, "high", "High", "market", "ohlcv", "main", timeframe),
        _series_meta(dataframe, "low", "Low", "market", "ohlcv", "main", timeframe),
        _series_meta(dataframe, "close", "Close", "market", "ohlcv", "main", timeframe),
        _series_meta(dataframe, "volume", "Volume", "market", "bar", "volume", timeframe),
    ]
    return ChartLayerMeta(
        id="market.ohlcv",
        source="market",
        status=_layer_status(series),
        label="Market Data",
        timeframe=timeframe,
        alignment="direct",
        series=series,
    )


def _build_watch_layer_meta(
    dataframe: DataFrame, plot_config: dict[str, Any], timeframe: str
) -> ChartLayerMeta:
    series = []
    for panel, column, config in _iter_plot_columns(plot_config):
        if not column.startswith("watch_"):
            continue
        series.append(
            _series_meta(
                dataframe,
                column,
                _watch_series_label(column),
                "watch",
                str(config.get("type", "line")),
                panel,
                timeframe,
                visible=config.get("hidden") is not True,
            )
        )

    return ChartLayerMeta(
        id="watch.indicators",
        source="watch",
        status=_layer_status(series),
        label="Watch Indicators",
        timeframe=timeframe,
        alignment="direct",
        series=series,
    )


def _build_strategy_layer_meta(
    dataframe: DataFrame,
    plot_config: dict[str, Any],
    strategy_name: str,
    strategy_timeframe: str | None,
    overlay: ChartOverlayMeta | None,
) -> ChartLayerMeta | None:
    if not strategy_timeframe:
        return None

    if overlay and overlay.hidden:
        return ChartLayerMeta(
            id="strategy.overlay",
            source="strategy",
            status="hidden" if overlay.alignment == "hidden" else "unavailable",
            label="Strategy Output",
            timeframe=strategy_timeframe,
            alignment=overlay.alignment,
            warnings=[overlay.warning] if overlay.warning else [],
        )

    series = []
    prefix = f"strategy_{strategy_timeframe}_"
    for panel, column, config in _iter_plot_columns(plot_config):
        if not column.startswith(prefix):
            continue
        original = column.removeprefix(prefix)
        series.append(
            _series_meta(
                dataframe,
                column,
                f"{original} - Strategy Output - {strategy_name}",
                "strategy",
                str(config.get("type", "line")),
                panel,
                strategy_timeframe,
            )
        )

    if not series:
        return None

    return ChartLayerMeta(
        id="strategy.overlay",
        source="strategy",
        status=_layer_status(series),
        label="Strategy Output",
        timeframe=strategy_timeframe,
        alignment=overlay.alignment if overlay else None,
        series=series,
    )


def _series_meta(
    dataframe: DataFrame,
    column: str,
    label: str,
    source: str,
    kind: str,
    panel: str,
    timeframe: str | None,
    visible: bool = True,
) -> ChartSeriesMeta:
    return ChartSeriesMeta(
        column=column,
        label=label,
        source=source,
        kind=kind,
        panel=panel,
        timeframe=timeframe,
        visible=visible,
        coverage=_series_coverage(dataframe, column),
    )


def _series_coverage(dataframe: DataFrame, column: str) -> ChartSeriesCoverage:
    total_points = len(dataframe)
    if column not in dataframe.columns:
        return ChartSeriesCoverage(total_points=total_points, reason="column unavailable")

    valid_mask = dataframe[column].notna()
    valid_points = int(valid_mask.sum())
    if valid_points == 0:
        return ChartSeriesCoverage(
            total_points=total_points,
            reason="no valid values in returned window",
        )

    valid_dates = dataframe.loc[valid_mask, "date"]
    return ChartSeriesCoverage(
        first_valid=_date_string(valid_dates.iloc[0]),
        last_valid=_date_string(valid_dates.iloc[-1]),
        valid_points=valid_points,
        total_points=total_points,
        reason="partial coverage" if valid_points < total_points else None,
    )


def _layer_status(series: list[ChartSeriesMeta]) -> str:
    if any(item.coverage.valid_points < item.coverage.total_points for item in series):
        return "partial"
    return "ok"


def _iter_plot_columns(plot_config: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    result: list[tuple[str, str, dict[str, Any]]] = []
    main_plot = plot_config.get("main_plot", {}) if isinstance(plot_config, dict) else {}
    if isinstance(main_plot, dict):
        for column, config in main_plot.items():
            result.append(("main", column, config if isinstance(config, dict) else {}))

    subplots = plot_config.get("subplots", {}) if isinstance(plot_config, dict) else {}
    if isinstance(subplots, dict):
        for subplot_name, subplot_columns in subplots.items():
            if not isinstance(subplot_columns, dict):
                continue
            for column, config in subplot_columns.items():
                result.append(
                    (str(subplot_name), column, config if isinstance(config, dict) else {})
                )
    return result


def _date_string(value: Any) -> str:
    return str(pd.to_datetime(value, utc=True))


def _watch_series_label(column: str) -> str:
    if column.startswith("watch_macd"):
        return f"{column.removeprefix('watch_').upper()} - Watch"
    if column.startswith("watch_ma"):
        return f"MA({column.removeprefix('watch_ma')}) - Watch"
    if column.startswith("watch_rsi"):
        return f"RSI({column.removeprefix('watch_rsi')}) - Watch"
    if column.startswith("watch_qqe_mod_"):
        label = column.removeprefix("watch_qqe_mod_").replace("_", " ").title()
        return f"QQE MOD {label} - Watch"
    if column.startswith("watch_supertrend_"):
        label = column.removeprefix("watch_supertrend_").replace("_", " ").title()
        return f"Supertrend {label} - Watch"
    return f"{column} - Watch"


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


def _last_candle_complete(dataframe: DataFrame, timeframe: str) -> bool:
    if dataframe.empty:
        return True
    candle_open = pd.to_datetime(dataframe.iloc[-1]["date"], utc=True)
    candle_close_ms = int(candle_open.timestamp() * 1000) + timeframe_to_msecs(timeframe)
    return candle_close_ms <= dt_ts(dt_now())


def _ensure_signal_columns(dataframe: DataFrame) -> DataFrame:
    result = dataframe.copy()
    for column in SIGNAL_COLUMNS:
        if column not in result.columns:
            result[column] = 0
        else:
            result[column] = result[column].fillna(0)
    return result
