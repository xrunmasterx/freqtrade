from __future__ import annotations

from typing import Any

import pandas as pd
from pandas import DataFrame

from freqtrade.research.side_data.models import (
    ResearchDocument,
    ResearchEvent,
    ResearchSideLayerSelection,
)
from freqtrade.research.side_data.store import LocalResearchSideDataStore
from freqtrade.rpc.api_server.api_schemas import (
    ChartLayerMeta,
    ChartLayerPoint,
    ChartSeriesCoverage,
    ChartSeriesMeta,
)


_FEATURE_LAYER_CONFIG = {
    "fund_flow_daily": {
        "label": "Fund Flow",
        "panel": "Fund Flow",
        "kind": "bar",
        "timeframe": "1d",
    }
}
_EVENT_LAYER_LABELS = {
    "limit_pool": "Limit Pool",
}
_DOCUMENT_LAYER_LABELS = {
    "announcements": "Announcements",
}


def apply_side_data_chart_layers(
    dataframe: DataFrame,
    store: LocalResearchSideDataStore,
    instrument_key: str,
    selection: ResearchSideLayerSelection,
) -> tuple[DataFrame, dict[str, Any], list[ChartLayerMeta]]:
    result = dataframe.copy()
    plot_update: dict[str, Any] = {"main_plot": {}, "subplots": {}}
    layers: list[ChartLayerMeta] = []
    window = _window_bounds(result)

    for dataset in selection.features:
        result, feature_columns = _apply_feature_dataset(result, store, instrument_key, dataset)
        if feature_columns:
            panel = _feature_config(dataset)["panel"]
            plot_update["subplots"].setdefault(panel, {})
            for column in feature_columns:
                plot_update["subplots"][panel][column] = {"type": _feature_config(dataset)["kind"]}
        layers.append(_build_feature_layer(result, dataset, feature_columns))

    for dataset in selection.events:
        events = store.load_events(instrument_key, [dataset])
        layers.append(_build_event_layer(dataset, _events_in_window(events, window)))

    for dataset in selection.documents:
        try:
            documents = store.load_documents(instrument_key, [dataset])
        except FileNotFoundError:
            documents = []
        layers.append(_build_document_layer(dataset, _documents_in_window(documents, window)))

    return result, plot_update, layers


def _apply_feature_dataset(
    dataframe: DataFrame,
    store: LocalResearchSideDataStore,
    instrument_key: str,
    dataset: str,
) -> tuple[DataFrame, list[str]]:
    try:
        feature_frame = store.load_feature_frame(instrument_key, [dataset])
    except FileNotFoundError:
        return dataframe, []

    feature_columns = [column for column in feature_frame.columns if column.startswith("feature_")]
    if not feature_columns:
        return dataframe, []

    merged = dataframe.copy()
    merged["date"] = pd.to_datetime(merged["date"], utc=True)
    right = feature_frame.loc[:, ["date", *feature_columns]].copy()
    right["date"] = pd.to_datetime(right["date"], utc=True)
    merged = merged.merge(right, on="date", how="left")
    return merged, feature_columns


def _build_feature_layer(
    dataframe: DataFrame,
    dataset: str,
    feature_columns: list[str],
) -> ChartLayerMeta:
    config = _feature_config(dataset)
    series = [
        ChartSeriesMeta(
            column=column,
            label=_feature_series_label(column, dataset),
            source="feature",
            kind=config["kind"],
            panel=config["panel"],
            timeframe=config["timeframe"],
            coverage=_series_coverage(dataframe, column),
        )
        for column in feature_columns
    ]
    return ChartLayerMeta(
        id=f"feature.{dataset}",
        source="feature",
        status=_feature_layer_status(series),
        label=config["label"],
        timeframe=config["timeframe"],
        alignment="candle_open",
        series=series,
    )


def _build_event_layer(dataset: str, events: list[ResearchEvent]) -> ChartLayerMeta:
    return ChartLayerMeta(
        id=f"event.{dataset}",
        source="event",
        status="ok" if events else "unavailable",
        label=_EVENT_LAYER_LABELS.get(dataset, _label_from_id(dataset)),
        timeframe="1d",
        alignment="effective_candle_time",
        points=[
            ChartLayerPoint(
                timestamp=_timestamp_ms(event.effective_candle_time),
                label=event.event_type,
                payload={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "title": event.title,
                    "publish_time": event.publish_time,
                    "source": event.source,
                    **event.payload,
                },
            )
            for event in events
        ],
    )


def _build_document_layer(dataset: str, documents: list[ResearchDocument]) -> ChartLayerMeta:
    return ChartLayerMeta(
        id=f"document.{dataset}",
        source="document",
        status="ok" if documents else "unavailable",
        label=_DOCUMENT_LAYER_LABELS.get(dataset, _label_from_id(dataset)),
        timeframe="1d",
        alignment="effective_candle_time",
        points=[
            ChartLayerPoint(
                timestamp=_timestamp_ms(document.effective_candle_time),
                label=document.document_type,
                payload={
                    "document_id": document.document_id,
                    "document_type": document.document_type,
                    "title": document.title,
                    "publish_time": document.publish_time,
                    "source": document.source,
                    "url": document.url,
                    **document.payload,
                },
            )
            for document in documents
        ],
    )


def _feature_config(dataset: str) -> dict[str, str]:
    return _FEATURE_LAYER_CONFIG.get(
        dataset,
        {
            "label": _label_from_id(dataset),
            "panel": _label_from_id(dataset),
            "kind": "line",
            "timeframe": "1d",
        },
    )


def _feature_series_label(column: str, dataset: str) -> str:
    prefix = f"feature_{dataset}_"
    field = column.removeprefix(prefix) if column.startswith(prefix) else column
    return _label_from_id(field)


def _feature_layer_status(series: list[ChartSeriesMeta]) -> str:
    if not series:
        return "unavailable"
    if all(item.coverage.valid_points == 0 for item in series):
        return "unavailable"
    if any(item.coverage.valid_points < item.coverage.total_points for item in series):
        return "partial"
    return "ok"


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


def _events_in_window(
    events: list[ResearchEvent],
    window: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> list[ResearchEvent]:
    return [event for event in events if _timestamp_in_window(event.effective_candle_time, window)]


def _documents_in_window(
    documents: list[ResearchDocument],
    window: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> list[ResearchDocument]:
    return [
        document
        for document in documents
        if _timestamp_in_window(document.effective_candle_time, window)
    ]


def _window_bounds(dataframe: DataFrame) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if dataframe.empty:
        return None, None

    dates = pd.to_datetime(dataframe["date"], utc=True)
    return dates.iloc[0], dates.iloc[-1]


def _timestamp_in_window(
    value: str,
    window: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> bool:
    start, stop = window
    if start is None or stop is None:
        return False

    timestamp = pd.to_datetime(value, utc=True)
    return start <= timestamp <= stop


def _timestamp_ms(value: str) -> int:
    return int(pd.to_datetime(value, utc=True).timestamp() * 1000)


def _date_string(value: Any) -> str:
    return str(pd.to_datetime(value, utc=True))


def _label_from_id(value: str) -> str:
    return value.replace("_", " ").title()
