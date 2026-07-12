import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from freqtrade.markets import CapabilityName, MarketType, ProductType, default_catalog_snapshot
from freqtrade.markets.default_catalog import CatalogSnapshot
from freqtrade.platform import SqlCatalogRepository, StaticCatalogRepository


def test_static_catalog_repository_returns_the_exact_snapshot() -> None:
    snapshot = default_catalog_snapshot()
    repository = StaticCatalogRepository(snapshot)

    assert repository.current() is snapshot


def test_sql_catalog_repository_round_trips_an_immutable_revision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlCatalogRepository(engine)
    repository.initialize_schema()
    snapshot = default_catalog_snapshot()

    repository.publish(snapshot, created_at=datetime(2026, 7, 12, tzinfo=UTC))

    assert repository.current() == snapshot
    with pytest.raises(ValueError, match="catalog revision already exists"):
        repository.publish(snapshot, created_at=datetime(2026, 7, 12, tzinfo=UTC))


def test_sql_catalog_repository_uses_json_dump_and_model_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlCatalogRepository(engine)
    repository.initialize_schema()
    snapshot = default_catalog_snapshot()
    expected_payload = json.loads(snapshot.model_dump_json())
    dump_modes: list[str | None] = []
    validated_payloads: list[object] = []
    model_dump = CatalogSnapshot.model_dump
    model_validate = CatalogSnapshot.model_validate

    def record_model_dump(self: CatalogSnapshot, *, mode: str = "python", **kwargs: object) -> dict:
        dump_modes.append(mode)
        return model_dump(self, mode=mode, **kwargs)

    def record_model_validate(cls: type[CatalogSnapshot], payload: object) -> CatalogSnapshot:
        validated_payloads.append(payload)
        return model_validate(payload)

    monkeypatch.setattr(CatalogSnapshot, "model_dump", record_model_dump)
    monkeypatch.setattr(CatalogSnapshot, "model_validate", classmethod(record_model_validate))

    repository.publish(snapshot, created_at=datetime(2026, 7, 12, tzinfo=UTC))
    restored = repository.current()

    assert dump_modes == ["json"]
    assert validated_payloads == [expected_payload]
    assert restored is not snapshot
    decision = restored.capability(
        MarketType.DIGITAL_ASSET,
        ProductType.SPOT,
        CapabilityName.MARKET_DATA,
    )
    with pytest.raises(ValidationError, match="Instance is frozen"):
        decision.allowed = False


def test_sql_catalog_repository_requires_an_initialized_snapshot() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlCatalogRepository(engine)
    repository.initialize_schema()

    with pytest.raises(LookupError, match="market catalog is not initialized"):
        repository.current()


def test_sql_catalog_repository_returns_the_latest_revision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlCatalogRepository(engine)
    repository.initialize_schema()
    first = default_catalog_snapshot()
    second = first.model_copy(update={"revision_id": "builtin-market-catalog-v2"})

    repository.publish(first, created_at=datetime(2026, 7, 12, 1, tzinfo=UTC))
    repository.publish(second, created_at=datetime(2026, 7, 12, 2, tzinfo=UTC))

    assert repository.current() == second
