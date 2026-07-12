import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    inspect,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


BACKEND_ROOT = Path(__file__).parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic-platform.ini"
MIGRATIONS_ROOT = BACKEND_ROOT / "platform_migrations"
TEST_DATABASE_PATTERN = re.compile(r"^platform_test[a-z0-9_]*$")
POSTGRES_SKIP_REASON = "PLATFORM_TEST_POSTGRES_URL is required for PostgreSQL migrations"

EXPECTED_COLUMNS = {
    "platform_catalog_revisions": {
        "revision_id",
        "payload",
        "created_at",
    },
    "runtime_instances": {
        "instance_id",
        "instance_kind",
        "owner_kind",
        "owner_id",
        "owner_revision",
        "management_mode",
        "runtime_spec_revision_id",
        "environment",
        "state_allocation_id",
        "desired_state",
        "lifecycle_status",
        "failure_latched",
        "optimistic_version",
        "created_at",
        "retired_at",
    },
    "runtime_attempts": {
        "attempt_id",
        "instance_id",
        "attempt_number",
        "runtime_spec_revision_id",
        "adapter_template_revision_id",
        "resolved_secret_versions",
        "image_id",
        "root_commit",
        "backend_commit",
        "frontend_commit",
        "strategies_commit",
        "project_identity",
        "container_identity",
        "status",
        "health_result",
        "started_at",
        "stopped_at",
        "exit_code",
        "failure_code",
    },
    "runtime_lifecycle_jobs": {
        "job_id",
        "instance_id",
        "requested_action",
        "idempotency_key",
        "expected_instance_version",
        "status",
        "lease_owner",
        "lease_expires_at",
        "requested_at",
        "started_at",
        "completed_at",
        "failure_code",
    },
    "runtime_endpoints": {
        "endpoint_id",
        "instance_id",
        "attempt_id",
        "endpoint_kind",
        "internal_port",
        "protocol",
        "exposure_policy",
        "created_at",
    },
    "runtime_access_requests": {
        "request_id",
        "instance_id",
        "attempt_id",
        "route_policy_revision",
        "method",
        "idempotency_key",
        "status",
        "result_code",
        "requested_at",
        "completed_at",
    },
    "runtime_audit_events": {
        "audit_event_id",
        "actor_type",
        "request_id",
        "idempotency_key",
        "owner_kind",
        "owner_id",
        "owner_revision",
        "instance_id",
        "runtime_spec_revision_id",
        "adapter_template_revision_id",
        "action",
        "previous_state",
        "next_state",
        "result_code",
        "occurred_at",
        "provenance",
    },
}

EXPECTED_TABLES = set(EXPECTED_COLUMNS)
EXPECTED_NULLABLE_COLUMNS = {
    "platform_catalog_revisions": set(),
    "runtime_instances": {"retired_at"},
    "runtime_attempts": {
        "health_result",
        "started_at",
        "stopped_at",
        "exit_code",
        "failure_code",
    },
    "runtime_lifecycle_jobs": {
        "lease_owner",
        "lease_expires_at",
        "started_at",
        "completed_at",
        "failure_code",
    },
    "runtime_endpoints": set(),
    "runtime_access_requests": {"idempotency_key", "result_code", "completed_at"},
    "runtime_audit_events": {
        "idempotency_key",
        "owner_kind",
        "owner_id",
        "owner_revision",
        "instance_id",
        "runtime_spec_revision_id",
        "adapter_template_revision_id",
        "previous_state",
        "next_state",
    },
}
EXPECTED_STRING_LENGTHS = {
    "platform_catalog_revisions": {"revision_id": 128},
    "runtime_instances": {
        "instance_id": 128,
        "instance_kind": 128,
        "owner_kind": 128,
        "owner_id": 128,
        "owner_revision": 128,
        "management_mode": 128,
        "runtime_spec_revision_id": 128,
        "environment": 16,
        "state_allocation_id": 128,
        "desired_state": 32,
        "lifecycle_status": 32,
    },
    "runtime_attempts": {
        "attempt_id": 128,
        "instance_id": 128,
        "runtime_spec_revision_id": 128,
        "adapter_template_revision_id": 128,
        "image_id": 256,
        "root_commit": 64,
        "backend_commit": 64,
        "frontend_commit": 64,
        "strategies_commit": 64,
        "project_identity": 128,
        "container_identity": 128,
        "status": 32,
        "failure_code": 128,
    },
    "runtime_lifecycle_jobs": {
        "job_id": 128,
        "instance_id": 128,
        "requested_action": 32,
        "idempotency_key": 128,
        "status": 32,
        "lease_owner": 128,
        "failure_code": 128,
    },
    "runtime_endpoints": {
        "endpoint_id": 128,
        "instance_id": 128,
        "attempt_id": 128,
        "endpoint_kind": 128,
        "protocol": 16,
        "exposure_policy": 32,
    },
    "runtime_access_requests": {
        "request_id": 128,
        "instance_id": 128,
        "attempt_id": 128,
        "route_policy_revision": 128,
        "method": 16,
        "idempotency_key": 128,
        "status": 32,
        "result_code": 128,
    },
    "runtime_audit_events": {
        "audit_event_id": 128,
        "actor_type": 128,
        "request_id": 128,
        "idempotency_key": 128,
        "owner_kind": 128,
        "owner_id": 128,
        "owner_revision": 128,
        "instance_id": 128,
        "runtime_spec_revision_id": 128,
        "adapter_template_revision_id": 128,
        "action": 128,
        "result_code": 128,
    },
}
EXPECTED_INTEGER_COLUMNS = {
    "runtime_instances": {"optimistic_version"},
    "runtime_attempts": {"attempt_number", "exit_code"},
    "runtime_lifecycle_jobs": {"expected_instance_version"},
    "runtime_endpoints": {"internal_port"},
}
EXPECTED_BOOLEAN_COLUMNS = {"runtime_instances": {"failure_latched"}}
EXPECTED_JSON_COLUMNS = {
    "platform_catalog_revisions": {"payload"},
    "runtime_attempts": {"resolved_secret_versions", "health_result"},
    "runtime_audit_events": {"previous_state", "next_state", "provenance"},
}
EXPECTED_DATETIME_COLUMNS = {
    "platform_catalog_revisions": {"created_at"},
    "runtime_instances": {"created_at", "retired_at"},
    "runtime_attempts": {"started_at", "stopped_at"},
    "runtime_lifecycle_jobs": {
        "lease_expires_at",
        "requested_at",
        "started_at",
        "completed_at",
    },
    "runtime_endpoints": {"created_at"},
    "runtime_access_requests": {"requested_at", "completed_at"},
    "runtime_audit_events": {"occurred_at"},
}
EXPECTED_FOREIGN_KEYS = {
    "runtime_attempts": {
        ("fk_runtime_attempts_instance_id", ("instance_id",), "runtime_instances")
    },
    "runtime_lifecycle_jobs": {
        ("fk_runtime_lifecycle_jobs_instance_id", ("instance_id",), "runtime_instances")
    },
    "runtime_endpoints": {
        ("fk_runtime_endpoints_instance_id", ("instance_id",), "runtime_instances"),
        ("fk_runtime_endpoints_attempt_id", ("attempt_id",), "runtime_attempts"),
    },
    "runtime_access_requests": {
        ("fk_runtime_access_requests_instance_id", ("instance_id",), "runtime_instances"),
        ("fk_runtime_access_requests_attempt_id", ("attempt_id",), "runtime_attempts"),
    },
    "runtime_audit_events": {
        ("fk_runtime_audit_events_instance_id", ("instance_id",), "runtime_instances")
    },
}

EXPECTED_CHECKS = {
    "runtime_instances": {
        "ck_runtime_instances_owner_kind",
        "ck_runtime_instances_management_mode",
        "ck_runtime_instances_environment",
        "ck_runtime_instances_desired_state",
        "ck_runtime_instances_lifecycle_status",
        "ck_runtime_instances_optimistic_version",
    },
    "runtime_attempts": {
        "ck_runtime_attempts_attempt_number",
        "ck_runtime_attempts_status",
    },
    "runtime_lifecycle_jobs": {
        "ck_runtime_lifecycle_jobs_requested_action",
        "ck_runtime_lifecycle_jobs_expected_instance_version",
        "ck_runtime_lifecycle_jobs_status",
    },
    "runtime_endpoints": {
        "ck_runtime_endpoints_internal_port",
        "ck_runtime_endpoints_protocol",
        "ck_runtime_endpoints_exposure_policy",
    },
    "runtime_audit_events": {
        "ck_runtime_audit_events_owner_kind",
        "ck_runtime_audit_events_action",
    },
}

EXPECTED_UNIQUES = {
    "runtime_attempts": {"uq_runtime_attempt_instance_number"},
    "runtime_lifecycle_jobs": {"uq_runtime_job_instance_idempotency"},
    "runtime_endpoints": {"uq_runtime_endpoint_attempt_kind"},
}
EXPECTED_PARTIAL_INDEX_STATES = {
    "uq_runtime_attempt_active": {"pending", "validating", "launching", "healthy", "stopping"},
    "uq_runtime_job_active": {"pending", "claimed", "running"},
}


def _alembic_config(postgres_url: str) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    return config


def _reset_public_schema(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
    finally:
        engine.dispose()


@pytest.fixture
def postgres_url() -> str:
    raw_url = os.environ.get("PLATFORM_TEST_POSTGRES_URL")
    if raw_url is None:
        pytest.skip(POSTGRES_SKIP_REASON)
    parsed_url = make_url(raw_url)
    if parsed_url.get_backend_name() != "postgresql":
        raise RuntimeError("PostgreSQL is required for platform migration tests")
    database_name = parsed_url.database or ""
    if TEST_DATABASE_PATTERN.fullmatch(database_name) is None:
        raise RuntimeError("refusing to reset a non-test platform database")

    _reset_public_schema(raw_url)
    try:
        yield raw_url
    finally:
        _reset_public_schema(raw_url)


def _load_tables(postgres_url: str) -> tuple[Engine, dict[str, Table]]:
    engine = create_engine(postgres_url)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return engine, {name: metadata.tables[name] for name in EXPECTED_TABLES}


def _instance_values(instance_id: str = "instance-1", **updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "instance_id": instance_id,
        "instance_kind": "execution_worker",
        "owner_kind": "paper_probe",
        "owner_id": "owner-1",
        "owner_revision": "owner-revision-1",
        "management_mode": "supervisor",
        "runtime_spec_revision_id": "runtime-spec-1",
        "environment": "paper",
        "state_allocation_id": "state-allocation-1",
        "desired_state": "stopped",
        "lifecycle_status": "registered",
        "failure_latched": False,
        "optimistic_version": 0,
        "created_at": datetime(2026, 7, 12, tzinfo=UTC),
        "retired_at": None,
    }
    values.update(updates)
    return values


def _attempt_values(attempt_id: str = "attempt-1", **updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "attempt_id": attempt_id,
        "instance_id": "instance-1",
        "attempt_number": 1,
        "runtime_spec_revision_id": "runtime-spec-1",
        "adapter_template_revision_id": "adapter-template-1",
        "resolved_secret_versions": {"exchange": "secret-version-1"},
        "image_id": "sha256:image-1",
        "root_commit": "1" * 40,
        "backend_commit": "2" * 40,
        "frontend_commit": "3" * 40,
        "strategies_commit": "4" * 40,
        "project_identity": "project-1",
        "container_identity": "container-1",
        "status": "pending",
        "health_result": None,
        "started_at": None,
        "stopped_at": None,
        "exit_code": None,
        "failure_code": None,
    }
    values.update(updates)
    return values


def _job_values(job_id: str = "job-1", **updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "job_id": job_id,
        "instance_id": "instance-1",
        "requested_action": "start",
        "idempotency_key": "idempotency-1",
        "expected_instance_version": 0,
        "status": "pending",
        "lease_owner": None,
        "lease_expires_at": None,
        "requested_at": datetime(2026, 7, 12, tzinfo=UTC),
        "started_at": None,
        "completed_at": None,
        "failure_code": None,
    }
    values.update(updates)
    return values


def _endpoint_values(endpoint_id: str = "endpoint-1", **updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "endpoint_id": endpoint_id,
        "instance_id": "instance-1",
        "attempt_id": "attempt-1",
        "endpoint_kind": "application_http",
        "internal_port": 8080,
        "protocol": "http",
        "exposure_policy": "internal_only",
        "created_at": datetime(2026, 7, 12, tzinfo=UTC),
    }
    values.update(updates)
    return values


def _expect_integrity_error(engine: Engine, table: Table, values: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(insert(table).values(**values))


def test_alembic_configuration_contains_no_dsn_or_credentials() -> None:
    contents = ALEMBIC_CONFIG_PATH.read_text(encoding="utf-8")

    assert "sqlalchemy.url" not in contents
    assert "password" not in contents.lower()
    assert "postgresql://" not in contents
    assert "postgresql+psycopg://" not in contents


def test_migration_environment_has_only_the_test_url_fallback() -> None:
    contents = (MIGRATIONS_ROOT / "env.py").read_text(encoding="utf-8")

    environment_lookups = re.findall(r"os\.environ\.get\(\"([^\"]+)\"\)", contents)
    assert environment_lookups == ["PLATFORM_TEST_POSTGRES_URL"]


def test_migration_test_url_fallback_rejects_non_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_TEST_POSTGRES_URL", "sqlite://")

    with pytest.raises(RuntimeError, match="isolated test PostgreSQL database"):
        command.current(Config(str(ALEMBIC_CONFIG_PATH)))


def test_platform_sources_do_not_create_production_schema() -> None:
    source_roots = (BACKEND_ROOT / "freqtrade" / "platform", MIGRATIONS_ROOT)
    offenders = []
    for source_root in source_roots:
        for path in source_root.rglob("*.py"):
            if "metadata.create_all(" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []


def test_empty_postgres_upgrades_to_registry_head(postgres_url: str) -> None:
    command.upgrade(_alembic_config(postgres_url), "head")

    engine = create_engine(postgres_url)
    try:
        assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_registry_downgrades_and_upgrades_again(postgres_url: str) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(postgres_url)
    try:
        assert EXPECTED_TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    try:
        assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_preserves_non_empty_catalog(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    catalog_metadata = MetaData()
    catalog = Table(
        "platform_catalog_revisions",
        catalog_metadata,
        Column("revision_id", String(128), primary_key=True),
        Column("payload", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    try:
        catalog_metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                insert(catalog).values(
                    revision_id="catalog-revision-1",
                    payload={"schema_version": 1},
                    created_at=datetime(2026, 7, 12, tzinfo=UTC),
                )
            )
        command.upgrade(_alembic_config(postgres_url), "head")
        with engine.connect() as connection:
            row = connection.execute(catalog.select()).one()
        assert row.revision_id == "catalog-revision-1"
        assert row.payload == {"schema_version": 1}
    finally:
        engine.dispose()


def test_registry_schema_matches_exact_columns_constraints_and_indexes(
    postgres_url: str,
) -> None:
    command.upgrade(_alembic_config(postgres_url), "head")
    engine = create_engine(postgres_url)
    schema = inspect(engine)
    try:
        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            columns = {column["name"]: column for column in schema.get_columns(table_name)}
            assert set(columns) == expected_columns
            assert {name for name, column in columns.items() if column["nullable"]} == (
                EXPECTED_NULLABLE_COLUMNS[table_name]
            )
            for column_name, length in EXPECTED_STRING_LENGTHS.get(table_name, {}).items():
                column_type = columns[column_name]["type"]
                assert isinstance(column_type, String)
                assert column_type.length == length
            for column_name in EXPECTED_INTEGER_COLUMNS.get(table_name, set()):
                assert isinstance(columns[column_name]["type"], Integer)
            for column_name in EXPECTED_BOOLEAN_COLUMNS.get(table_name, set()):
                assert isinstance(columns[column_name]["type"], Boolean)
            for column_name in EXPECTED_JSON_COLUMNS.get(table_name, set()):
                assert isinstance(columns[column_name]["type"], JSON)
            for column_name in EXPECTED_DATETIME_COLUMNS.get(table_name, set()):
                column_type = columns[column_name]["type"]
                assert isinstance(column_type, DateTime)
                assert column_type.timezone is True

        for table_name, expected_foreign_keys in EXPECTED_FOREIGN_KEYS.items():
            actual = {
                (
                    foreign_key["name"],
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                )
                for foreign_key in schema.get_foreign_keys(table_name)
                if foreign_key["options"].get("ondelete") == "RESTRICT"
            }
            assert actual == expected_foreign_keys

        for table_name, expected_checks in EXPECTED_CHECKS.items():
            assert {check["name"] for check in schema.get_check_constraints(table_name)} == (
                expected_checks
            )

        for table_name, expected_uniques in EXPECTED_UNIQUES.items():
            assert {
                constraint["name"] for constraint in schema.get_unique_constraints(table_name)
            } == expected_uniques

        attempt_indexes = {index["name"]: index for index in schema.get_indexes("runtime_attempts")}
        job_indexes = {
            index["name"]: index for index in schema.get_indexes("runtime_lifecycle_jobs")
        }
        assert attempt_indexes["uq_runtime_attempt_active"]["unique"] is True
        assert job_indexes["uq_runtime_job_active"]["unique"] is True
        for index_name, index in (
            ("uq_runtime_attempt_active", attempt_indexes["uq_runtime_attempt_active"]),
            ("uq_runtime_job_active", job_indexes["uq_runtime_job_active"]),
        ):
            predicate = index["dialect_options"]["postgresql_where"]
            assert "status" in predicate
            assert (
                set(re.findall(r"'([a-z_]+)'", predicate))
                == (EXPECTED_PARTIAL_INDEX_STATES[index_name])
            )
    finally:
        engine.dispose()


def test_registry_rejects_unknown_closed_values_and_invalid_numbers(
    postgres_url: str,
) -> None:
    command.upgrade(_alembic_config(postgres_url), "head")
    engine, tables = _load_tables(postgres_url)
    try:
        invalid_instances = (
            _instance_values("bad-owner", owner_kind="unknown"),
            _instance_values("bad-mode", management_mode="compose"),
            _instance_values("bad-environment", environment="simulation"),
            _instance_values("bad-desired", desired_state="unknown"),
            _instance_values("bad-lifecycle", lifecycle_status="unknown"),
            _instance_values("bad-version", optimistic_version=-1),
        )
        for values in invalid_instances:
            _expect_integrity_error(engine, tables["runtime_instances"], values)

        with engine.begin() as connection:
            connection.execute(insert(tables["runtime_instances"]).values(**_instance_values()))

        _expect_integrity_error(
            engine,
            tables["runtime_attempts"],
            _attempt_values("bad-attempt-state", status="unknown"),
        )
        _expect_integrity_error(
            engine,
            tables["runtime_attempts"],
            _attempt_values("bad-attempt-number", attempt_number=0),
        )
        _expect_integrity_error(
            engine,
            tables["runtime_lifecycle_jobs"],
            _job_values("bad-action", requested_action="restart"),
        )
        _expect_integrity_error(
            engine,
            tables["runtime_lifecycle_jobs"],
            _job_values("bad-job-state", status="unknown"),
        )
        _expect_integrity_error(
            engine,
            tables["runtime_lifecycle_jobs"],
            _job_values("bad-job-version", expected_instance_version=-1),
        )

        with engine.begin() as connection:
            connection.execute(insert(tables["runtime_attempts"]).values(**_attempt_values()))

        for values in (
            _endpoint_values("bad-port-low", internal_port=0),
            _endpoint_values("bad-port-high", internal_port=65536),
            _endpoint_values("bad-protocol", protocol="tcp"),
            _endpoint_values("bad-exposure", exposure_policy="public"),
        ):
            _expect_integrity_error(engine, tables["runtime_endpoints"], values)
    finally:
        engine.dispose()


def test_registry_enforces_partial_and_ordinary_uniqueness(postgres_url: str) -> None:
    command.upgrade(_alembic_config(postgres_url), "head")
    engine, tables = _load_tables(postgres_url)
    try:
        with engine.begin() as connection:
            connection.execute(insert(tables["runtime_instances"]).values(**_instance_values()))
            connection.execute(insert(tables["runtime_attempts"]).values(**_attempt_values()))
            connection.execute(insert(tables["runtime_lifecycle_jobs"]).values(**_job_values()))

        _expect_integrity_error(
            engine,
            tables["runtime_attempts"],
            _attempt_values("attempt-2", attempt_number=2, status="healthy"),
        )
        _expect_integrity_error(
            engine,
            tables["runtime_attempts"],
            _attempt_values("attempt-3", attempt_number=1, status="stopped"),
        )
        _expect_integrity_error(
            engine,
            tables["runtime_lifecycle_jobs"],
            _job_values("job-2", idempotency_key="idempotency-2", status="running"),
        )
        _expect_integrity_error(
            engine,
            tables["runtime_lifecycle_jobs"],
            _job_values("job-3", idempotency_key="idempotency-1", status="succeeded"),
        )

        with engine.begin() as connection:
            connection.execute(
                insert(tables["runtime_attempts"]).values(
                    **_attempt_values("attempt-4", attempt_number=2, status="stopped")
                )
            )
            connection.execute(
                insert(tables["runtime_lifecycle_jobs"]).values(
                    **_job_values("job-4", idempotency_key="idempotency-4", status="succeeded")
                )
            )
            connection.execute(insert(tables["runtime_endpoints"]).values(**_endpoint_values()))

        _expect_integrity_error(
            engine,
            tables["runtime_endpoints"],
            _endpoint_values("endpoint-2"),
        )
    finally:
        engine.dispose()


def test_registry_enforces_restrictive_foreign_keys(postgres_url: str) -> None:
    command.upgrade(_alembic_config(postgres_url), "head")
    engine, tables = _load_tables(postgres_url)
    try:
        with engine.begin() as connection:
            connection.execute(insert(tables["runtime_instances"]).values(**_instance_values()))
            connection.execute(insert(tables["runtime_attempts"]).values(**_attempt_values()))
            connection.execute(insert(tables["runtime_endpoints"]).values(**_endpoint_values()))

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    tables["runtime_instances"]
                    .delete()
                    .where(tables["runtime_instances"].c.instance_id == "instance-1")
                )
    finally:
        engine.dispose()


def test_registry_json_round_trip_contains_only_evidence_columns(postgres_url: str) -> None:
    command.upgrade(_alembic_config(postgres_url), "head")
    engine, tables = _load_tables(postgres_url)
    try:
        with engine.begin() as connection:
            connection.execute(insert(tables["runtime_instances"]).values(**_instance_values()))
            connection.execute(insert(tables["runtime_attempts"]).values(**_attempt_values()))
            connection.execute(
                insert(tables["runtime_audit_events"]).values(
                    audit_event_id="audit-1",
                    actor_type="operator_cli",
                    request_id="request-1",
                    idempotency_key=None,
                    owner_kind="paper_probe",
                    owner_id="owner-1",
                    owner_revision="owner-revision-1",
                    instance_id="instance-1",
                    runtime_spec_revision_id="runtime-spec-1",
                    adapter_template_revision_id="adapter-template-1",
                    action="start",
                    previous_state={"desired_state": "stopped"},
                    next_state={"desired_state": "running"},
                    result_code="accepted",
                    occurred_at=datetime(2026, 7, 12, tzinfo=UTC),
                    provenance={"root_commit": "1" * 40},
                )
            )

        with engine.connect() as connection:
            attempt = connection.execute(tables["runtime_attempts"].select()).one()
            audit = connection.execute(tables["runtime_audit_events"].select()).one()
        assert attempt.resolved_secret_versions == {"exchange": "secret-version-1"}
        assert audit.provenance == {"root_commit": "1" * 40}

        forbidden_tokens = ("secret_value", "secret_path", "authorization", "cookie", "body")
        for columns in EXPECTED_COLUMNS.values():
            assert not any(
                token in column.lower() for token in forbidden_tokens for column in columns
            )
    finally:
        engine.dispose()


def test_alembic_head_has_no_orm_drift(postgres_url: str) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "head")

    command.check(config)
