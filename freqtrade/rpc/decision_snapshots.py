from datetime import datetime
from typing import Any

import pandas as pd
from pandas import DataFrame
from sqlalchemy import select

from freqtrade.persistence import DecisionSnapshot
from freqtrade.rpc.api_server.api_schemas import (
    ChartLayerMeta,
    ChartLayerPoint,
    ChartSeriesCoverage,
    ChartSeriesMeta,
)


def load_decision_snapshots_for_window(
    pair: str,
    timeframe: str,
    start: datetime,
    stop: datetime,
) -> list[DecisionSnapshot]:
    if not hasattr(DecisionSnapshot, "session"):
        return []

    return list(
        DecisionSnapshot.session.scalars(
            select(DecisionSnapshot)
            .where(
                DecisionSnapshot.pair == pair,
                DecisionSnapshot.timeframe == timeframe,
                DecisionSnapshot.candle_open >= start,
                DecisionSnapshot.candle_open <= stop,
            )
            .order_by(DecisionSnapshot.candle_open, DecisionSnapshot.decision_time)
        ).all()
    )


def build_decision_snapshot_layer(
    snapshots: list[DecisionSnapshot],
    chart_dataframe: DataFrame,
) -> ChartLayerMeta:
    series = [
        _series_meta(key, snapshots, chart_dataframe) for key in _snapshot_value_keys(snapshots)
    ]
    return ChartLayerMeta(
        id="decision_snapshot.evidence",
        source="decision_snapshot",
        status="ok" if snapshots else "unavailable",
        label="Bot Decision",
        timeframe=snapshots[0].timeframe if snapshots else None,
        alignment="candle_open",
        series=series,
        points=_snapshot_points(snapshots, chart_dataframe),
    )


def _snapshot_value_keys(snapshots: list[DecisionSnapshot]) -> list[str]:
    keys: list[str] = []
    for snapshot in snapshots:
        keys.extend(snapshot.values)
    return list(dict.fromkeys(keys))


def _series_meta(
    key: str,
    snapshots: list[DecisionSnapshot],
    chart_dataframe: DataFrame,
) -> ChartSeriesMeta:
    return ChartSeriesMeta(
        column=f"decision_snapshot_{key}",
        label=f"{_label_from_key(key)} - Decision Snapshot",
        source="decision_snapshot",
        kind=_series_kind(key, snapshots),
        panel="decision",
        timeframe=snapshots[0].timeframe if snapshots else None,
        coverage=_series_coverage(key, snapshots, chart_dataframe),
    )


def _series_kind(key: str, snapshots: list[DecisionSnapshot]) -> str:
    for snapshot in snapshots:
        value = snapshot.values.get(key)
        if value is not None:
            return "event" if isinstance(value, bool) else "line"
    return "line"


def _series_coverage(
    key: str,
    snapshots: list[DecisionSnapshot],
    chart_dataframe: DataFrame,
) -> ChartSeriesCoverage:
    chart_dates = {_date_key(value) for value in chart_dataframe.get("date", [])}
    valid_dates = sorted(
        {
            _date_key(snapshot.candle_open)
            for snapshot in snapshots
            if snapshot.values.get(key) is not None
            and _date_key(snapshot.candle_open) in chart_dates
        }
    )
    valid_points = len(valid_dates)
    total_points = len(chart_dataframe)
    if valid_points == 0:
        return ChartSeriesCoverage(
            total_points=total_points,
            reason="no aligned decision snapshots in returned window",
        )

    return ChartSeriesCoverage(
        first_valid=_date_string(valid_dates[0]),
        last_valid=_date_string(valid_dates[-1]),
        valid_points=valid_points,
        total_points=total_points,
        reason="partial coverage" if valid_points < total_points else None,
    )


def _snapshot_points(
    snapshots: list[DecisionSnapshot],
    chart_dataframe: DataFrame,
) -> list[ChartLayerPoint]:
    chart_dates = {_date_key(value) for value in chart_dataframe.get("date", [])}
    return [
        ChartLayerPoint(
            timestamp=int(_date_key(snapshot.candle_open).timestamp() * 1000),
            label=snapshot.decision,
            payload={
                "decision": snapshot.decision,
                "decision_time": _date_string(snapshot.decision_time),
                "strategy": snapshot.strategy,
                "snapshot_type": snapshot.snapshot_type,
                "values": snapshot.values,
                "context": snapshot.context,
            },
        )
        for snapshot in snapshots
        if _date_key(snapshot.candle_open) in chart_dates
    ]


def _date_key(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True).as_unit("ms")


def _date_string(value: Any) -> str:
    return str(pd.to_datetime(value, utc=True))


def _label_from_key(key: str) -> str:
    acronyms = {"qqe", "rsi"}
    return " ".join(
        part.upper() if part.lower() in acronyms else part.title() for part in key.split("_")
    )
