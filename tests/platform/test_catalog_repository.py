import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from freqtrade.markets import CapabilityName, MarketType, ProductType, default_catalog_snapshot
from freqtrade.markets.default_catalog import CatalogSnapshot
from freqtrade.platform import SqlCatalogRepository, StaticCatalogRepository
from freqtrade.platform import catalog_repository as catalog_repository_module
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.database import PlatformBase


def _create_catalog_schema(engine: Engine) -> None:
    PlatformBase.metadata.create_all(
        engine,
        tables=[CatalogRevisionRecord.__table__],
    )


def test_static_catalog_repository_returns_the_exact_snapshot() -> None:
    snapshot = default_catalog_snapshot()
    repository = StaticCatalogRepository(snapshot)

    assert repository.current() is snapshot
    assert repository.get(snapshot.revision_id) is snapshot

    with pytest.raises(LookupError, match="market catalog revision not found"):
        repository.get("missing-revision")


def test_sql_catalog_repository_round_trips_an_immutable_revision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_catalog_schema(engine)
    repository = SqlCatalogRepository(engine)
    snapshot = default_catalog_snapshot()

    repository.publish(snapshot, created_at=datetime(2026, 7, 12, tzinfo=UTC))

    assert repository.current() == snapshot
    with pytest.raises(ValueError, match="catalog revision already exists"):
        repository.publish(snapshot, created_at=datetime(2026, 7, 12, tzinfo=UTC))


def test_sql_catalog_repository_uses_json_dump_and_model_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_catalog_schema(engine)
    repository = SqlCatalogRepository(engine)
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
    policy = restored.product_policies[0]
    with pytest.raises(TypeError, match="does not support item assignment"):
        policy.decisions[CapabilityName.MARKET_DATA] = decision
    with pytest.raises(ValidationError, match="Instance is frozen"):
        decision.allowed = False


@pytest.mark.parametrize(
    ("invalid_policy_form", "expected_message"),
    [
        ("duplicate", "duplicate product capability policy"),
        ("dangling", "policy references unknown product"),
        ("missing", "product is missing capability policy"),
    ],
)
def test_sql_catalog_repository_rejects_invalid_stored_policy_payload(
    invalid_policy_form: str,
    expected_message: str,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_catalog_schema(engine)
    payload = default_catalog_snapshot().model_dump(mode="json")
    policies = payload["product_policies"]
    if invalid_policy_form == "duplicate":
        policies.append(policies[0])
    elif invalid_policy_form == "dangling":
        policies.append(
            {
                "market_id": "a_share",
                "product_id": "warrant",
                "decisions": {},
            }
        )
    else:
        policies.pop()
    with engine.begin() as connection:
        connection.execute(
            insert(CatalogRevisionRecord).values(
                revision_id=f"invalid-{invalid_policy_form}",
                payload=payload,
                created_at=datetime(2026, 7, 12, tzinfo=UTC),
            )
        )

    repository = SqlCatalogRepository(engine)

    with pytest.raises(ValidationError, match=expected_message):
        repository.current()


def test_sql_catalog_repository_requires_an_initialized_snapshot() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_catalog_schema(engine)
    repository = SqlCatalogRepository(engine)

    with pytest.raises(LookupError, match="market catalog is not initialized"):
        repository.current()


def test_sql_catalog_repository_does_not_initialize_production_schema() -> None:
    assert not hasattr(SqlCatalogRepository, "initialize_schema")


def test_sql_catalog_repository_returns_the_latest_revision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_catalog_schema(engine)
    repository = SqlCatalogRepository(engine)
    first = default_catalog_snapshot()
    second = first.model_copy(update={"revision_id": "builtin-market-catalog-v3"})

    repository.publish(first, created_at=datetime(2026, 7, 12, 1, tzinfo=UTC))
    repository.publish(second, created_at=datetime(2026, 7, 12, 2, tzinfo=UTC))

    assert repository.current() == second
    assert repository.get(first.revision_id) == first
    assert repository.get(second.revision_id) == second


def test_sql_catalog_repository_get_requires_an_exact_initialized_revision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_catalog_schema(engine)
    repository = SqlCatalogRepository(engine)

    with pytest.raises(LookupError, match="market catalog revision not found"):
        repository.get("missing-revision")


def test_sql_catalog_repository_rejects_naive_created_at_before_opening_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlCatalogRepository(engine)
    snapshot = default_catalog_snapshot()

    def fail_if_session_is_opened(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("Session opened before created_at validation")

    monkeypatch.setattr(catalog_repository_module, "Session", fail_if_session_is_opened)

    with pytest.raises(ValueError, match="created_at must be timezone-aware"):
        repository.publish(snapshot, created_at=datetime(2026, 7, 12))


def test_sql_catalog_repository_normalizes_offset_aware_times_before_ordering() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_catalog_schema(engine)
    repository = SqlCatalogRepository(engine)
    first = default_catalog_snapshot()
    second = first.model_copy(update={"revision_id": "builtin-market-catalog-v3"})

    repository.publish(
        first,
        created_at=datetime(2026, 7, 12, 9, tzinfo=timezone(timedelta(hours=8))),
    )
    repository.publish(
        second,
        created_at=datetime(2026, 7, 11, 21, tzinfo=timezone(-timedelta(hours=4))),
    )

    assert repository.current() == second


def test_sql_catalog_repository_translates_duplicate_revision_commit_race(
    tmp_path: Path,
) -> None:
    database_path = (tmp_path / "catalog.db").as_posix()
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    _create_catalog_schema(engine)
    repository = SqlCatalogRepository(engine)
    snapshot = default_catalog_snapshot()
    competitor_inserted = False

    @event.listens_for(engine, "before_cursor_execute")
    def insert_competing_revision(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal competitor_inserted
        if competitor_inserted or not statement.lstrip().startswith(
            "INSERT INTO platform_catalog_revisions"
        ):
            return
        competitor_inserted = True
        with engine.begin() as competing_connection:
            competing_connection.execute(
                insert(CatalogRevisionRecord).values(
                    revision_id=snapshot.revision_id,
                    payload=snapshot.model_dump(mode="json"),
                    created_at=datetime(2026, 7, 12, tzinfo=UTC),
                )
            )

    with pytest.raises(ValueError, match="catalog revision already exists"):
        repository.publish(snapshot, created_at=datetime(2026, 7, 12, tzinfo=UTC))

    assert competitor_inserted
    assert repository.current() == snapshot


def test_sql_catalog_repository_reraises_unrelated_integrity_error() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_catalog_schema(engine)
    repository = SqlCatalogRepository(engine)
    snapshot = default_catalog_snapshot()
    forced_error = IntegrityError("forced INSERT failure", {}, RuntimeError("not a duplicate"))

    @event.listens_for(engine, "before_cursor_execute")
    def fail_catalog_insert(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("INSERT INTO platform_catalog_revisions"):
            raise forced_error

    with pytest.raises(IntegrityError) as exc_info:
        repository.publish(snapshot, created_at=datetime(2026, 7, 12, tzinfo=UTC))

    assert exc_info.value is forced_error
