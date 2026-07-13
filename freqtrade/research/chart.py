from __future__ import annotations

import math
from datetime import datetime
from numbers import Integral, Real
from typing import Any

import pandas as pd
from pandas import DataFrame

from freqtrade.markets import MarketType
from freqtrade.research import ResearchBotProfile, create_research_data_source
from freqtrade.research.a_share_timeframes import is_a_share_minute_timeframe
from freqtrade.research.exceptions import ResearchUnsupportedFeatureError
from freqtrade.research.side_data.chart_layers import apply_side_data_chart_layers
from freqtrade.research.side_data.store import LocalResearchSideDataStore
from freqtrade.research.windowing import apply_research_timerange
from freqtrade.rpc.api_server.api_schemas import (
    ChartAxisMeta,
    ChartLayerMeta,
    ChartResponseMeta,
    ChartSeriesCoverage,
    ChartSeriesMeta,
    ChartWindowMeta,
    ResearchChartCandlesRequest,
)
from freqtrade.rpc.chart_indicators import add_watch_indicators, build_watch_plot_config
from freqtrade.util.datetime_helpers import dt_now


SIGNAL_COLUMNS = ("enter_long", "exit_long", "enter_short", "exit_short")
_TIMEFRAME_SECONDS = {
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
    "M": 30 * 24 * 60 * 60,
}


def build_research_chart_candles_response(
    profile: ResearchBotProfile,
    payload: ResearchChartCandlesRequest,
) -> dict[str, Any]:
    data_source = create_research_data_source(profile)
    dataframe = data_source.load_ohlcv(
        payload.instrument,
        payload.timeframe,
        payload.adjustment,
    )
    provenance = data_source.get_ohlcv_provenance(
        payload.instrument,
        payload.timeframe,
        payload.adjustment,
    )
    dataframe = apply_research_timerange(dataframe, payload.timerange)
    dataframe = dataframe.tail(payload.limit).reset_index(drop=True)
    dataframe = add_watch_indicators(dataframe, payload.watch_indicators)
    plot_config = build_watch_plot_config(payload.watch_indicators)
    side_layers: list[ChartLayerMeta] = []
    if payload.side_layers and is_a_share_minute_timeframe(payload.timeframe):
        raise ResearchUnsupportedFeatureError("Research side layers support 1d only.")
    if payload.side_layers and profile.side_data is not None and profile.side_data_root is not None:
        dataframe, side_plot_config, side_layers = apply_side_data_chart_layers(
            dataframe,
            LocalResearchSideDataStore(
                profile.side_data_root,
                enabled_datasets=profile.side_data.enabled_datasets,
            ),
            payload.instrument,
            payload.side_layers,
        )
        _merge_plot_config(plot_config, side_plot_config)
    meta = _build_research_chart_response_meta(
        dataframe,
        profile,
        payload,
        plot_config,
        provenance,
        side_layers=side_layers,
    )

    response = _convert_research_dataframe_to_dict(
        instrument=payload.instrument,
        timeframe=payload.timeframe,
        dataframe=dataframe,
        axis_meta=meta.axis,
        last_analyzed=dt_now(),
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


def _convert_research_dataframe_to_dict(
    instrument: str,
    timeframe: str,
    dataframe: DataFrame,
    axis_meta: ChartAxisMeta | None,
    last_analyzed: datetime,
) -> dict[str, Any]:
    response_dataframe = dataframe.copy()
    original_columns = list(response_dataframe.columns)
    signals = dict.fromkeys(SIGNAL_COLUMNS, 0)

    if not response_dataframe.empty:
        dates = pd.to_datetime(response_dataframe["date"], utc=True, errors="coerce")
        response_dataframe.loc[:, "date"] = dates
        response_dataframe.loc[:, "__date_ts"] = [_date_ts(value) for value in dates]

        for signal_column in SIGNAL_COLUMNS:
            if signal_column not in response_dataframe.columns:
                continue
            mask = response_dataframe[signal_column] == 1
            signals[signal_column] = int(mask.sum())
            response_dataframe.loc[mask, f"_{signal_column}_signal_close"] = response_dataframe.loc[
                mask, "close"
            ]

    if axis_meta is not None and axis_meta.source_column not in response_dataframe.columns:
        response_dataframe.loc[:, axis_meta.source_column] = []

    if (
        axis_meta is not None
        and axis_meta.mode == "trading_session"
        and axis_meta.display_column is not None
    ):
        response_dataframe.loc[:, axis_meta.display_column] = range(len(response_dataframe))

    result = {
        "pair": instrument,
        "timeframe": timeframe,
        "timeframe_ms": _timeframe_to_msecs(timeframe),
        "strategy": "",
        "all_columns": original_columns,
        "columns": list(response_dataframe.columns),
        "data": [
            [_json_safe_value(value) for value in row]
            for row in response_dataframe.itertuples(index=False, name=None)
        ],
        "length": len(response_dataframe),
        "buy_signals": signals["enter_long"],
        "sell_signals": signals["exit_long"],
        "enter_long_signals": signals["enter_long"],
        "exit_long_signals": signals["exit_long"],
        "enter_short_signals": signals["enter_short"],
        "exit_short_signals": signals["exit_short"],
        "last_analyzed": last_analyzed,
        "last_analyzed_ts": int(last_analyzed.timestamp()),
        "data_start": "",
        "data_start_ts": 0,
        "data_stop": "",
        "data_stop_ts": 0,
        "annotations": [],
    }
    if not response_dataframe.empty:
        first_date = response_dataframe.iloc[0]["date"]
        last_date = response_dataframe.iloc[-1]["date"]
        result.update(
            {
                "data_start": "" if pd.isna(first_date) else str(first_date),
                "data_start_ts": _date_ts(first_date) or 0,
                "data_stop": "" if pd.isna(last_date) else str(last_date),
                "data_stop_ts": _date_ts(last_date) or 0,
            }
        )
    return result


def _build_research_chart_response_meta(
    dataframe: DataFrame,
    profile: ResearchBotProfile,
    payload: ResearchChartCandlesRequest,
    plot_config: dict[str, Any],
    provenance: Any,
    side_layers: list[ChartLayerMeta] | None = None,
) -> ChartResponseMeta:
    layers = [
        _build_market_layer_meta(dataframe, payload.timeframe),
        _build_watch_layer_meta(dataframe, plot_config, payload.timeframe),
        *(side_layers or []),
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
        axis=_chart_axis_meta(profile),
        layers=layers,
        warnings=list(dict.fromkeys(warnings)),
        data_provenance=provenance.model_dump(),
    )


def _chart_axis_meta(profile: ResearchBotProfile) -> ChartAxisMeta:
    if profile.market == MarketType.A_SHARE:
        return ChartAxisMeta(
            mode="trading_session",
            source_column="__date_ts",
            display_column="__display_x",
            timezone="Asia/Shanghai",
        )
    return ChartAxisMeta(mode="time", source_column="__date_ts")


def _merge_plot_config(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key in ("main_plot", "subplots"):
        target_section = target.setdefault(key, {})
        update_section = update.get(key, {})
        if not isinstance(update_section, dict):
            continue
        for name, value in update_section.items():
            if isinstance(value, dict) and isinstance(target_section.get(name), dict):
                target_section[name].update(value)
            else:
                target_section[name] = value


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


def _timeframe_to_msecs(timeframe: str) -> int:
    try:
        factor = _TIMEFRAME_SECONDS[timeframe[-1]]
        value = int(timeframe[:-1])
    except (KeyError, ValueError):
        raise ValueError("Invalid research timeframe") from None
    return value * factor * 1000


def _date_ts(value: Any) -> int | None:
    if pd.isna(value):
        return None
    return int(pd.to_datetime(value, utc=True).timestamp() * 1000)


def _date_string(value: Any) -> str:
    return str(pd.to_datetime(value, utc=True))


def _json_safe_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    if isinstance(value, Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        return number if math.isfinite(number) else None
    return value
