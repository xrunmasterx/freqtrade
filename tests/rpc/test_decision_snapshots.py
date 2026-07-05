from datetime import UTC, datetime

import pandas as pd

from freqtrade.persistence import DecisionSnapshot
from freqtrade.rpc.decision_snapshots import (
    build_decision_snapshot_layer,
    load_decision_snapshots_for_window,
)


def _snapshot(
    pair: str,
    timeframe: str,
    candle_open: datetime,
    decision_time: datetime,
    decision: str = "hold",
) -> DecisionSnapshot:
    return DecisionSnapshot(
        pair=pair,
        timeframe=timeframe,
        candle_open=candle_open,
        decision_time=decision_time,
        strategy="SampleStrategy",
        snapshot_type="live",
        decision=decision,
        values={"rsi": 42.5},
        context={},
    )


def test_load_decision_snapshots_for_window_filters_and_orders(init_persistence):
    window_start = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    window_stop = datetime(2026, 7, 5, 9, 2, tzinfo=UTC)
    in_window_late_decision = _snapshot(
        "BTC/USDT",
        "1m",
        datetime(2026, 7, 5, 9, 1, tzinfo=UTC),
        datetime(2026, 7, 5, 9, 1, 5, tzinfo=UTC),
        "hold",
    )
    in_window_early_decision = _snapshot(
        "BTC/USDT",
        "1m",
        datetime(2026, 7, 5, 9, 1, tzinfo=UTC),
        datetime(2026, 7, 5, 9, 1, 1, tzinfo=UTC),
        "enter_long",
    )
    start_boundary = _snapshot(
        "BTC/USDT",
        "1m",
        window_start,
        datetime(2026, 7, 5, 9, 0, 1, tzinfo=UTC),
        "start_boundary",
    )
    stop_boundary = _snapshot(
        "BTC/USDT",
        "1m",
        window_stop,
        datetime(2026, 7, 5, 9, 2, 1, tzinfo=UTC),
        "stop_boundary",
    )
    out_of_window = _snapshot(
        "BTC/USDT",
        "1m",
        datetime(2026, 7, 5, 9, 3, tzinfo=UTC),
        datetime(2026, 7, 5, 9, 3, 1, tzinfo=UTC),
        "out_of_window",
    )
    other_pair = _snapshot(
        "ETH/USDT",
        "1m",
        window_start,
        datetime(2026, 7, 5, 9, 0, 2, tzinfo=UTC),
        "other_pair",
    )
    other_timeframe = _snapshot(
        "BTC/USDT",
        "5m",
        window_start,
        datetime(2026, 7, 5, 9, 0, 3, tzinfo=UTC),
        "other_timeframe",
    )
    DecisionSnapshot.session.add_all(
        [
            out_of_window,
            in_window_late_decision,
            other_pair,
            stop_boundary,
            in_window_early_decision,
            other_timeframe,
            start_boundary,
        ]
    )
    DecisionSnapshot.session.commit()

    result = load_decision_snapshots_for_window(
        "BTC/USDT",
        "1m",
        window_start,
        window_stop,
    )

    assert [snapshot.decision for snapshot in result] == [
        "start_boundary",
        "enter_long",
        "hold",
        "stop_boundary",
    ]


def test_load_decision_snapshots_for_window_returns_empty_for_no_matches(init_persistence):
    DecisionSnapshot.session.add(
        _snapshot(
            "ETH/USDT",
            "1m",
            datetime(2026, 7, 5, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 5, 9, 0, 1, tzinfo=UTC),
        )
    )
    DecisionSnapshot.session.commit()

    result = load_decision_snapshots_for_window(
        "BTC/USDT",
        "1m",
        datetime(2026, 7, 5, 9, 0, tzinfo=UTC),
        datetime(2026, 7, 5, 9, 2, tzinfo=UTC),
    )

    assert result == []


def test_load_decision_snapshots_for_window_returns_empty_without_session(monkeypatch):
    monkeypatch.delattr(DecisionSnapshot, "session", raising=False)

    result = load_decision_snapshots_for_window(
        "BTC/USDT",
        "1m",
        datetime(2026, 7, 5, 9, 0, tzinfo=UTC),
        datetime(2026, 7, 5, 9, 2, tzinfo=UTC),
    )

    assert result == []


def test_build_decision_snapshot_layer_aligns_snapshot_values_to_chart_frame():
    first_candle = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    second_candle = datetime(2026, 7, 5, 9, 1, tzinfo=UTC)
    chart_dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    first_candle,
                    second_candle,
                    datetime(2026, 7, 5, 9, 2, tzinfo=UTC),
                ],
                utc=True,
            ),
            "close": [100.0, 101.0, 102.0],
        }
    )
    snapshots = [
        DecisionSnapshot(
            pair="BTC/USDT",
            timeframe="1m",
            candle_open=first_candle,
            decision_time=datetime(2026, 7, 5, 9, 0, 3, tzinfo=UTC),
            strategy="SampleStrategy",
            snapshot_type="live",
            decision="hold",
            values={"rsi": 42.5},
            context={},
        ),
        DecisionSnapshot(
            pair="BTC/USDT",
            timeframe="1m",
            candle_open=second_candle,
            decision_time=datetime(2026, 7, 5, 9, 1, 3, tzinfo=UTC),
            strategy="SampleStrategy",
            snapshot_type="live",
            decision="enter_long",
            values={"rsi": 45.0, "qqe_mod_up_state": True},
            context={},
        ),
    ]

    layer = build_decision_snapshot_layer(snapshots, chart_dataframe)

    assert layer.source == "decision_snapshot"
    assert layer.status == "ok"
    assert layer.label == "Bot Decision"
    assert layer.alignment == "candle_open"
    series_by_column = {series.column: series for series in layer.series}
    assert series_by_column["decision_snapshot_rsi"].label == "RSI - Decision Snapshot"
    assert series_by_column["decision_snapshot_rsi"].coverage.valid_points == 2
    assert series_by_column["decision_snapshot_rsi"].coverage.total_points == 3
    assert series_by_column["decision_snapshot_rsi"].coverage.reason == "partial coverage"
    assert (
        series_by_column["decision_snapshot_qqe_mod_up_state"].label
        == "QQE Mod Up State - Decision Snapshot"
    )
    assert series_by_column["decision_snapshot_qqe_mod_up_state"].coverage.valid_points == 1


def test_build_decision_snapshot_layer_includes_aligned_points_with_payload():
    first_candle = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    second_candle = datetime(2026, 7, 5, 9, 1, tzinfo=UTC)
    outside_candle = datetime(2026, 7, 5, 9, 3, tzinfo=UTC)
    chart_dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime([first_candle, second_candle], utc=True),
            "close": [100.0, 101.0],
        }
    )
    snapshots = [
        DecisionSnapshot(
            pair="BTC/USDT",
            timeframe="1m",
            candle_open=first_candle,
            decision_time=datetime(2026, 7, 5, 9, 0, 3, tzinfo=UTC),
            strategy="SampleStrategy",
            snapshot_type="live",
            decision="hold",
            values={"rsi": 42.5},
            context={"reason": "warmup"},
        ),
        DecisionSnapshot(
            pair="BTC/USDT",
            timeframe="1m",
            candle_open=outside_candle,
            decision_time=datetime(2026, 7, 5, 9, 3, 3, tzinfo=UTC),
            strategy="SampleStrategy",
            snapshot_type="live",
            decision="outside",
            values={"rsi": 48.0},
            context={"reason": "outside"},
        ),
    ]

    layer = build_decision_snapshot_layer(snapshots, chart_dataframe)

    assert len(layer.points) == 1
    point = layer.points[0]
    assert point.timestamp == int(first_candle.timestamp() * 1000)
    assert point.label == "hold"
    assert point.payload["decision"] == "hold"
    assert point.payload["decision_time"] == "2026-07-05 09:00:03+00:00"
    assert point.payload["strategy"] == "SampleStrategy"
    assert point.payload["snapshot_type"] == "live"
    assert point.payload["values"] == {"rsi": 42.5}
    assert point.payload["context"] == {"reason": "warmup"}
