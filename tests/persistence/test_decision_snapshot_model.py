from datetime import UTC, datetime

from sqlalchemy import select

from freqtrade.persistence import DecisionSnapshot


def test_decision_snapshot_persists_json_payloads_and_links(init_persistence):
    candle_open = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    decision_time = datetime(2026, 7, 5, 9, 0, 5, tzinfo=UTC)
    snapshot = DecisionSnapshot(
        trade_id=10,
        order_id="order-123",
        pair="BTC/USDT",
        timeframe="1m",
        candle_open=candle_open,
        decision_time=decision_time,
        strategy="SampleStrategy",
        strategy_version="v1",
        config_hash="config-hash",
        snapshot_type="live",
        decision="enter_long",
        values={"rsi": 42.5, "qqe_mod_up_state": True},
        context={"stake_amount": 100, "reason": "signal"},
    )

    DecisionSnapshot.session.add(snapshot)
    DecisionSnapshot.session.commit()

    stored = DecisionSnapshot.session.scalars(
        select(DecisionSnapshot).where(DecisionSnapshot.pair == "BTC/USDT")
    ).one()

    assert stored.trade_id == 10
    assert stored.order_id == "order-123"
    assert stored.pair == "BTC/USDT"
    assert stored.timeframe == "1m"
    assert stored.candle_open.replace(tzinfo=UTC) == candle_open
    assert stored.decision_time.replace(tzinfo=UTC) == decision_time
    assert stored.strategy == "SampleStrategy"
    assert stored.strategy_version == "v1"
    assert stored.config_hash == "config-hash"
    assert stored.snapshot_type == "live"
    assert stored.decision == "enter_long"
    assert stored.values["rsi"] == 42.5
    assert stored.values["qqe_mod_up_state"] is True
    assert stored.context == {"stake_amount": 100, "reason": "signal"}
