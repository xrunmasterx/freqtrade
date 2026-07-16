import contextlib
import io
import re
import runpy
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from pathlib import Path

import alembic
import pytest
import sqlalchemy
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
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
    select,
)
from sqlalchemy.exc import IntegrityError

from freqtrade.platform.runtime_repository import SqlRuntimeRepository
from tests.platform import postgres_test_support
from tests.platform.postgres_test_support import (
    RedactedPostgresUrl as _RedactedPostgresUrl,
)
from tests.platform.postgres_test_support import (
    reset_public_schema as _reset_public_schema,
)
from tests.platform.postgres_test_support import (
    validate_test_database_url as _validate_test_database_url,
)


BACKEND_ROOT = Path(__file__).parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic-platform.ini"
MIGRATIONS_ROOT = BACKEND_ROOT / "platform_migrations"
REGISTRATION_MIGRATION_PATH = (
    MIGRATIONS_ROOT / "versions" / "20260714_0004_runtime_registration.py"
)
LEASE_GENERATION_MIGRATION_PATH = (
    MIGRATIONS_ROOT / "versions" / "20260717_0005_runtime_lease_generation.py"
)
STALE_JOB_INDEX_MIGRATION_PATH = (
    MIGRATIONS_ROOT / "versions" / "20260717_0006_runtime_stale_job_index.py"
)
STATE_TIMESTAMPS_MIGRATION_PATH = (
    MIGRATIONS_ROOT / "versions" / "20260717_0007_state_allocation_timestamps.py"
)

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
        "lease_generation",
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
RUNTIME_TABLES = EXPECTED_TABLES - {"platform_catalog_revisions"}
RUNTIME_PARENT_TABLES = {
    "adapter_template_revisions",
    "runtime_spec_revisions",
    "state_allocations",
}
RUNTIME_SPEC_PAYLOAD_DIGEST = "b" * 64
RUNTIME_SPEC_REVISION_ID = f"runtime-spec-{RUNTIME_SPEC_PAYLOAD_DIGEST}"
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
    "runtime_lifecycle_jobs": {"expected_instance_version", "lease_generation"},
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
    "runtime_instances": {
        (
            "fk_runtime_instances_runtime_spec_revision_id",
            ("runtime_spec_revision_id",),
            "runtime_spec_revisions",
        ),
        (
            "fk_runtime_instances_state_allocation_id",
            ("state_allocation_id",),
            "state_allocations",
        ),
    },
    "runtime_attempts": {
        ("fk_runtime_attempts_instance_id", ("instance_id",), "runtime_instances"),
        (
            "fk_runtime_attempts_runtime_spec_revision_id",
            ("runtime_spec_revision_id",),
            "runtime_spec_revisions",
        ),
        (
            "fk_runtime_attempts_adapter_template_revision_id",
            ("adapter_template_revision_id",),
            "adapter_template_revisions",
        ),
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
        ("fk_runtime_audit_events_instance_id", ("instance_id",), "runtime_instances"),
        (
            "fk_runtime_audit_events_runtime_spec_revision_id",
            ("runtime_spec_revision_id",),
            "runtime_spec_revisions",
        ),
        (
            "fk_runtime_audit_events_adapter_template_revision_id",
            ("adapter_template_revision_id",),
            "adapter_template_revisions",
        ),
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
        "ck_runtime_lifecycle_jobs_lease_generation",
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


def _load_tables(postgres_url: str) -> tuple[Engine, dict[str, Table]]:
    engine = create_engine(postgres_url)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    table_names = EXPECTED_TABLES | RUNTIME_PARENT_TABLES
    return engine, {name: metadata.tables[name] for name in table_names}


def _seed_runtime_parent_chain(connection: sqlalchemy.Connection, tables: dict[str, Table]) -> None:
    connection.execute(
        insert(tables["platform_catalog_revisions"]).values(
            revision_id="catalog-revision-1",
            payload={"schema_version": 1},
            created_at=datetime(2026, 7, 12, tzinfo=UTC),
        )
    )
    connection.execute(
        insert(tables["adapter_template_revisions"]).values(
            adapter_template_revision_id="adapter-template-1",
            template_id="adapter-template-1",
            semantic_version="1.0.0",
            canonical_payload="{}",
            payload_digest="a" * 64,
            source_commit="1" * 40,
            root_commit="1" * 40,
            backend_commit="2" * 40,
            frontend_commit="3" * 40,
            strategies_commit="4" * 40,
            status="active",
            published_by="platform-test",
            published_at=datetime(2026, 7, 12, tzinfo=UTC),
            deprecated_at=None,
            revoked_at=None,
        )
    )
    connection.execute(
        insert(tables["state_allocations"]).values(
            state_allocation_id="state-allocation-1",
            instance_id="fixture-parent-instance",
            layout_id="fixture-layout-1",
            provider_id="managed-local-v1",
            relative_path="ft_userdata/runtime/instances/fixture-parent-instance",
            kind="fresh",
            status="ready",
            generation=1,
            restore_source_bundle_id=None,
            created_at=datetime(2026, 7, 12, tzinfo=UTC),
            ready_at=datetime(2026, 7, 12, tzinfo=UTC),
            retired_at=None,
        )
    )
    connection.execute(
        insert(tables["runtime_spec_revisions"]).values(
            runtime_spec_revision_id=RUNTIME_SPEC_REVISION_ID,
            owner_kind="paper_probe",
            owner_id="owner-1",
            owner_revision="owner-revision-1",
            instance_kind="execution_worker",
            catalog_revision_id="catalog-revision-1",
            environment="paper",
            adapter_template_revision_id="adapter-template-1",
            state_allocation_id="state-allocation-1",
            canonical_payload="{}",
            payload_digest=RUNTIME_SPEC_PAYLOAD_DIGEST,
            created_at=datetime(2026, 7, 12, tzinfo=UTC),
        )
    )


def _instance_values(instance_id: str = "instance-1", **updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "instance_id": instance_id,
        "instance_kind": "execution_worker",
        "owner_kind": "paper_probe",
        "owner_id": "owner-1",
        "owner_revision": "owner-revision-1",
        "management_mode": "supervisor",
        "runtime_spec_revision_id": RUNTIME_SPEC_REVISION_ID,
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
        "runtime_spec_revision_id": RUNTIME_SPEC_REVISION_ID,
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
        "lease_generation": 0,
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


def test_test_database_url_repr_redacts_secret() -> None:
    raw_url = "postgresql+psycopg://platform_test:sensitive@127.0.0.1/platform_test_safe"

    test_url = _RedactedPostgresUrl(raw_url)

    assert str(test_url) == raw_url
    assert "sensitive" not in repr(test_url)
    assert raw_url not in repr(test_url)


def test_test_database_guard_rejects_dbname_override_without_secret_in_error() -> None:
    password = "guard-regression-password"
    unsafe_url = (
        f"postgresql+psycopg://platform_test:{password}@127.0.0.1/"
        "platform_test_safe?dbname=production"
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_test_database_url(unsafe_url)

    assert unsafe_url not in str(exc_info.value)
    assert password not in str(exc_info.value)


def test_reset_rejects_dbname_override_before_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_attempted = False

    def fail_if_connection_is_attempted(_url: str) -> Engine:
        nonlocal connection_attempted
        connection_attempted = True
        raise AssertionError("database connection attempted before URL validation")

    monkeypatch.setattr(
        postgres_test_support,
        "create_engine",
        fail_if_connection_is_attempted,
    )

    with pytest.raises(RuntimeError):
        _reset_public_schema(
            "postgresql+psycopg://platform_test@127.0.0.1/platform_test_safe?dbname=production"
        )

    assert connection_attempted is False


def test_reset_verifies_effective_database_before_schema_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements = []

    class FakeResult:
        def scalar_one(self) -> str:
            return "production"

    class FakeConnection:
        def exec_driver_sql(self, statement: str) -> FakeResult:
            statements.append(statement)
            if statement == "SELECT current_database()":
                return FakeResult()
            raise AssertionError("schema DDL executed before effective database validation")

    class FakeEngine:
        @contextlib.contextmanager
        def begin(self):
            yield FakeConnection()

        def dispose(self) -> None:
            pass

    monkeypatch.setattr(postgres_test_support, "create_engine", lambda _url: FakeEngine())

    with pytest.raises(RuntimeError, match="refusing to reset a non-test platform database"):
        _reset_public_schema("postgresql+psycopg://platform_test@127.0.0.1/platform_test_safe")

    assert statements == ["SELECT current_database()"]


def test_migration_test_fallback_rejects_dbname_override_before_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "migration-guard-password"
    unsafe_url = (
        f"postgresql+psycopg://platform_test:{password}@127.0.0.1/"
        "platform_test_safe?dbname=production"
    )
    connection_attempted = False

    def fail_if_connection_is_attempted(*_args: object, **_kwargs: object) -> Engine:
        nonlocal connection_attempted
        connection_attempted = True
        raise AssertionError("database connection attempted before URL validation")

    monkeypatch.setenv("PLATFORM_TEST_POSTGRES_URL", unsafe_url)
    monkeypatch.setattr(sqlalchemy, "create_engine", fail_if_connection_is_attempted)

    with pytest.raises(RuntimeError) as exc_info:
        command.current(Config(str(ALEMBIC_CONFIG_PATH)))

    assert connection_attempted is False
    assert unsafe_url not in str(exc_info.value)
    assert password not in str(exc_info.value)


def test_migration_test_fallback_verifies_effective_database_before_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []

    class FakeResult:
        def scalar_one(self) -> str:
            return "production"

    class FakeConnection:
        def __enter__(self):
            events.append("connect")
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def exec_driver_sql(self, statement: str) -> FakeResult:
            events.append(statement)
            return FakeResult()

    class FakeEngine:
        def connect(self) -> FakeConnection:
            return FakeConnection()

        def dispose(self) -> None:
            events.append("dispose")

    class FakeConfig:
        def get_main_option(self, _name: str) -> None:
            return None

    class FakeContext:
        config = FakeConfig()

        def is_offline_mode(self) -> bool:
            return False

        def configure(self, **_kwargs: object) -> None:
            events.append("configure")

        @contextlib.contextmanager
        def begin_transaction(self):
            yield

        def run_migrations(self) -> None:
            events.append("run_migrations")

    monkeypatch.setenv(
        "PLATFORM_TEST_POSTGRES_URL",
        "postgresql+psycopg://platform_test@127.0.0.1/platform_test_safe",
    )
    monkeypatch.setattr(alembic, "context", FakeContext())
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *_args, **_kwargs: FakeEngine())

    with pytest.raises(RuntimeError, match="isolated test PostgreSQL database"):
        runpy.run_path(str(MIGRATIONS_ROOT / "env.py"))

    assert events == ["connect", "SELECT current_database()", "dispose"]


def test_offline_upgrade_emits_complete_registry_sql_without_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()
    config = Config(str(ALEMBIC_CONFIG_PATH), output_buffer=output)
    config.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://offline.invalid/platform_test_offline",
    )
    connection_attempted = False

    def fail_if_connection_is_attempted(*_args: object, **_kwargs: object) -> Engine:
        nonlocal connection_attempted
        connection_attempted = True
        raise AssertionError("offline migration attempted a database connection")

    monkeypatch.setattr(sqlalchemy, "create_engine", fail_if_connection_is_attempted)

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert connection_attempted is False
    for table_name in EXPECTED_TABLES:
        assert re.search(
            rf"CREATE TABLE(?: IF NOT EXISTS)? (?:public\.)?{table_name}\b",
            sql,
        )
    assert "incompatible_platform_catalog_revisions" in sql
    assert sql.index("incompatible_platform_catalog_revisions") < sql.index(
        "CREATE TABLE runtime_instances"
    )
    assert "offline.invalid" not in sql
    assert "postgresql+psycopg" not in sql
    assert "password" not in sql.lower()


def test_offline_migrations_pin_public_schema_and_preserve_catalog_on_downgrade() -> None:
    upgrade_output = io.StringIO()
    upgrade_config = Config(str(ALEMBIC_CONFIG_PATH), output_buffer=upgrade_output)
    upgrade_config.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://offline.invalid/platform_test_offline",
    )
    command.upgrade(upgrade_config, "head", sql=True)
    upgrade_sql = upgrade_output.getvalue()

    downgrade_output = io.StringIO()
    downgrade_config = Config(str(ALEMBIC_CONFIG_PATH), output_buffer=downgrade_output)
    downgrade_config.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://offline.invalid/platform_test_offline",
    )
    command.downgrade(downgrade_config, "head:base", sql=True)
    downgrade_sql = downgrade_output.getvalue()

    controlled_search_path = "SET LOCAL search_path TO public, pg_catalog"
    assert controlled_search_path in upgrade_sql
    assert upgrade_sql.index(controlled_search_path) < upgrade_sql.index(
        "CREATE TABLE runtime_instances"
    )
    assert controlled_search_path in downgrade_sql
    assert "DROP TABLE platform_catalog_revisions" not in downgrade_sql
    assert "DROP TABLE public.platform_catalog_revisions" not in downgrade_sql
    assert "CREATE TABLE public.alembic_version" in upgrade_sql


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


def test_test_url_fallback_upgrade_commits_registry_head(postgres_url: str) -> None:
    command.upgrade(Config(str(ALEMBIC_CONFIG_PATH)), "head")

    engine = create_engine(postgres_url)
    try:
        schema = inspect(engine)
        assert EXPECTED_TABLES <= set(schema.get_table_names())
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "20260717_0007"
            )
    finally:
        engine.dispose()


def test_registry_downgrades_and_upgrades_again(postgres_url: str) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(postgres_url)
    try:
        table_names = set(inspect(engine).get_table_names(schema="public"))
        assert RUNTIME_TABLES.isdisjoint(table_names)
        assert "platform_catalog_revisions" in table_names
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


def test_downgrade_preserves_adopted_non_empty_catalog_exactly(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    catalog_metadata = MetaData()
    catalog = Table(
        "platform_catalog_revisions",
        catalog_metadata,
        Column("revision_id", String(128), primary_key=True),
        Column("payload", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema="public",
    )
    expected_created_at = datetime(2026, 7, 13, tzinfo=UTC)
    try:
        catalog_metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                insert(catalog).values(
                    revision_id="phase1-catalog-revision",
                    payload={"schema_version": 1, "source": "phase1"},
                    created_at=expected_created_at,
                )
            )

        config = _alembic_config(postgres_url)
        command.upgrade(config, "head")
        command.downgrade(config, "base")

        with engine.connect() as connection:
            row = connection.execute(catalog.select()).one()
        assert row.revision_id == "phase1-catalog-revision"
        assert row.payload == {"schema_version": 1, "source": "phase1"}
        assert row.created_at == expected_created_at
        table_names = set(inspect(engine).get_table_names(schema="public"))
        assert RUNTIME_TABLES.isdisjoint(table_names)
    finally:
        engine.dispose()


def test_caller_search_path_cannot_redirect_migration_state(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    shadow_schema = "phase2a_shadow"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {shadow_schema}")
        redirected_url = sqlalchemy.engine.make_url(postgres_url).update_query_dict(
            {"options": f"-csearch_path={shadow_schema},public"}
        )
        command.upgrade(
            _alembic_config(redirected_url.render_as_string(hide_password=False)), "head"
        )

        schema = inspect(engine)
        assert EXPECTED_TABLES <= set(schema.get_table_names(schema="public"))
        assert schema.get_table_names(schema=shadow_schema) == []
        with engine.connect() as connection:
            version = connection.exec_driver_sql(
                "SELECT version_num FROM public.alembic_version"
            ).scalar_one()
        assert version == "20260717_0007"
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {shadow_schema} CASCADE")
        engine.dispose()


@pytest.mark.parametrize(
    "catalog_ddl",
    [
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) PRIMARY KEY, payload json NOT NULL)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) PRIMARY KEY, payload json NOT NULL, "
            "created_at timestamptz NOT NULL, extra text)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(127) PRIMARY KEY, payload json NOT NULL, "
            "created_at timestamptz NOT NULL)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) PRIMARY KEY, payload jsonb NOT NULL, "
            "created_at timestamptz NOT NULL)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) PRIMARY KEY, payload json, "
            "created_at timestamptz NOT NULL)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) NOT NULL, payload json NOT NULL, "
            "created_at timestamptz NOT NULL)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) NOT NULL, payload json NOT NULL, "
            "created_at timestamptz PRIMARY KEY)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) PRIMARY KEY, payload json NOT NULL, "
            "created_at timestamptz NOT NULL, CONSTRAINT unexpected_catalog_check "
            "CHECK (revision_id <> 'forbidden'))"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) PRIMARY KEY, payload json NOT NULL, "
            "created_at timestamptz NOT NULL DEFAULT now())"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) GENERATED ALWAYS AS ('generated') STORED "
            "PRIMARY KEY, payload json NOT NULL, created_at timestamptz NOT NULL)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "payload json NOT NULL, created_at timestamptz NOT NULL)"
        ),
        (
            "CREATE TABLE platform_catalog_revisions ("
            "revision_id varchar(128) PRIMARY KEY, payload json NOT NULL, "
            "created_at timestamptz NOT NULL); CREATE INDEX unexpected_catalog_index "
            "ON platform_catalog_revisions (created_at)"
        ),
    ],
    ids=[
        "missing-column",
        "extra-column",
        "wrong-length",
        "wrong-type",
        "wrong-nullability",
        "missing-primary-key",
        "wrong-primary-key",
        "extra-constraint",
        "default",
        "generated",
        "identity",
        "extra-index",
    ],
)
def test_upgrade_rejects_incompatible_catalog_before_registry_ddl_or_stamp(
    postgres_url: str,
    catalog_ddl: str,
) -> None:
    engine = create_engine(postgres_url)
    try:
        with engine.begin() as connection:
            for statement in catalog_ddl.split("; "):
                connection.exec_driver_sql(statement)

        with pytest.raises(sqlalchemy.exc.DBAPIError) as exc_info:
            command.upgrade(_alembic_config(postgres_url), "head")

        message = str(exc_info.value)
        assert "incompatible_platform_catalog_revisions" in message
        assert "password" not in message.lower()
        assert set(inspect(engine).get_table_names()) == {"platform_catalog_revisions"}
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
        stale_index = job_indexes["ix_runtime_job_stale_reconciliation"]
        assert stale_index["unique"] is False
        assert tuple(stale_index["column_names"]) == (
            "status",
            "failure_code",
            "completed_at",
            "job_id",
        )
        assert "postgresql_where" not in stale_index["dialect_options"]
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
        with engine.begin() as connection:
            _seed_runtime_parent_chain(connection, tables)

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
            _seed_runtime_parent_chain(connection, tables)
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
            _seed_runtime_parent_chain(connection, tables)
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
            _seed_runtime_parent_chain(connection, tables)
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
                    runtime_spec_revision_id=RUNTIME_SPEC_REVISION_ID,
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


def test_lease_generation_migration_requires_quiesced_active_jobs(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "20260714_0004")
    engine, tables = _load_tables(postgres_url)
    try:
        job_values = _job_values(
            status="claimed",
            lease_owner="supervisor-1",
            lease_expires_at=datetime(2026, 7, 17, tzinfo=UTC),
            started_at=datetime(2026, 7, 17, tzinfo=UTC),
        )
        job_values.pop("lease_generation")
        with engine.begin() as connection:
            _seed_runtime_parent_chain(connection, tables)
            connection.execute(insert(tables["runtime_instances"]).values(**_instance_values()))
            connection.execute(insert(tables["runtime_lifecycle_jobs"]).values(**job_values))

        with pytest.raises(
            sqlalchemy.exc.DBAPIError,
            match="runtime_lease_generation_requires_quiescence",
        ):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "20260714_0004"
            )
        assert "lease_generation" not in {
            column["name"] for column in inspect(engine).get_columns("runtime_lifecycle_jobs")
        }
    finally:
        engine.dispose()


def test_lease_generation_migration_blocks_concurrent_legacy_claim(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "20260714_0004")
    engine, tables = _load_tables(postgres_url)
    attempt_lock_connection = engine.connect()
    attempt_lock_transaction = attempt_lock_connection.begin()
    try:
        job_values = _job_values()
        job_values.pop("lease_generation")
        with engine.begin() as connection:
            _seed_runtime_parent_chain(connection, tables)
            connection.execute(insert(tables["runtime_instances"]).values(**_instance_values()))
            connection.execute(insert(tables["runtime_lifecycle_jobs"]).values(**job_values))

        attempt_lock_connection.exec_driver_sql(
            "LOCK TABLE public.runtime_attempts IN ACCESS SHARE MODE"
        )

        def legacy_claim() -> str | None:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    job_id = connection.exec_driver_sql(
                        "SELECT job_id FROM public.runtime_lifecycle_jobs "
                        "WHERE status = 'pending' ORDER BY requested_at, job_id "
                        "FOR UPDATE SKIP LOCKED LIMIT 1"
                    ).scalar_one_or_none()
                finally:
                    transaction.rollback()
                return job_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            migration = executor.submit(command.upgrade, config, "head")
            claim = None
            try:
                deadline = time.monotonic() + 5
                migration_has_job_lock = False
                while time.monotonic() < deadline:
                    with engine.connect() as connection:
                        migration_has_job_lock = bool(
                            connection.exec_driver_sql(
                                "SELECT EXISTS ("
                                "SELECT 1 FROM pg_locks AS locks "
                                "JOIN pg_class AS relation ON relation.oid = locks.relation "
                                "JOIN pg_namespace AS namespace "
                                "ON namespace.oid = relation.relnamespace "
                                "WHERE namespace.nspname = 'public' "
                                "AND relation.relname = 'runtime_lifecycle_jobs' "
                                "AND locks.mode = 'AccessExclusiveLock' AND locks.granted)"
                            ).scalar_one()
                        )
                    if migration_has_job_lock:
                        break
                    time.sleep(0.01)
                assert migration_has_job_lock

                claim = executor.submit(legacy_claim)
                with pytest.raises(FutureTimeoutError):
                    claim.result(timeout=0.2)
            finally:
                if attempt_lock_transaction.is_active:
                    attempt_lock_transaction.commit()

            migration.result(timeout=5)
            assert claim is not None
            assert claim.result(timeout=5) == "job-1"

        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "20260717_0007"
            )
            row = connection.exec_driver_sql(
                "SELECT status, lease_generation FROM public.runtime_lifecycle_jobs "
                "WHERE job_id = 'job-1'"
            ).one()
        assert row == ("pending", 0)
    finally:
        if attempt_lock_transaction.is_active:
            attempt_lock_transaction.rollback()
        attempt_lock_connection.close()
        engine.dispose()


def test_lease_generation_migration_normalizes_legacy_health_and_guards_downgrade(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "20260714_0004")
    engine, tables = _load_tables(postgres_url)
    try:
        with engine.begin() as connection:
            _seed_runtime_parent_chain(connection, tables)
            connection.execute(insert(tables["runtime_instances"]).values(**_instance_values()))
            connection.execute(
                insert(tables["runtime_attempts"]).values(
                    **_attempt_values(
                        status="healthy",
                        health_result={"result_code": "healthy"},
                        image_id=f"sha256:{'a' * 64}",
                        started_at=None,
                    )
                )
            )

        command.upgrade(config, "head")
        recovery = SqlRuntimeRepository(engine).get_latest_attempt_material("instance-1")
        assert recovery is not None
        assert recovery.started_at is None
        assert recovery.health_result is not None
        assert recovery.health_result.profile_id == "legacy-runtime-health-v1"
        assert recovery.health_result.attempts == 1
        assert recovery.health_result.result_code == "health_probe_healthy"
        assert recovery.health_result.last_failure_code is None
        assert recovery.health_result.observed_at == datetime(1970, 1, 1, tzinfo=UTC)
        generation_column = next(
            column
            for column in inspect(engine).get_columns("runtime_lifecycle_jobs")
            if column["name"] == "lease_generation"
        )
        assert generation_column["nullable"] is False
        assert generation_column["default"] is None

        with pytest.raises(sqlalchemy.exc.DBAPIError, match="runtime_task6_downgrade_refused"):
            command.downgrade(config, "20260714_0004")

        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "20260717_0007"
            )
        preserved = SqlRuntimeRepository(engine).get_latest_attempt_material("instance-1")
        assert preserved is not None
        assert preserved.health_result == recovery.health_result
    finally:
        engine.dispose()


def test_lease_generation_downgrade_rejects_nonzero_generation_without_legacy_health(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "head")
    engine, tables = _load_tables(postgres_url)
    try:
        with engine.begin() as connection:
            _seed_runtime_parent_chain(connection, tables)
            connection.execute(insert(tables["runtime_instances"]).values(**_instance_values()))
            connection.execute(
                insert(tables["runtime_lifecycle_jobs"]).values(
                    **_job_values(
                        status="succeeded",
                        lease_generation=1,
                        completed_at=datetime(2026, 7, 17, tzinfo=UTC),
                    )
                )
            )

        with pytest.raises(sqlalchemy.exc.DBAPIError, match="runtime_task6_downgrade_refused"):
            command.downgrade(config, "20260714_0004")

        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "20260717_0007"
            )
            generation = connection.exec_driver_sql(
                "SELECT lease_generation FROM public.runtime_lifecycle_jobs "
                "WHERE job_id = 'job-1'"
            ).scalar_one()
            legacy_count = connection.exec_driver_sql(
                "SELECT count(*) FROM public.runtime_attempts "
                "WHERE health_result ->> 'profile_id' = 'legacy-runtime-health-v1'"
            ).scalar_one()
        assert generation == 1
        assert legacy_count == 0
    finally:
        engine.dispose()


def test_runtime_registration_migration_is_linear_and_single_head() -> None:
    migration = runpy.run_path(str(REGISTRATION_MIGRATION_PATH))

    assert migration["revision"] == "20260714_0004"
    assert migration["down_revision"] == "20260714_0003"
    assert ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG_PATH))).get_heads() == [
        "20260717_0007"
    ]

    lease_migration = runpy.run_path(str(LEASE_GENERATION_MIGRATION_PATH))
    assert lease_migration["revision"] == "20260717_0005"
    assert lease_migration["down_revision"] == "20260714_0004"
    assert "'observed_at'" in lease_migration["_NORMALIZE_LEGACY_HEALTH_SQL"]
    stale_index_migration = runpy.run_path(str(STALE_JOB_INDEX_MIGRATION_PATH))
    assert stale_index_migration["revision"] == "20260717_0006"
    assert stale_index_migration["down_revision"] == "20260717_0005"
    state_timestamps_migration = runpy.run_path(str(STATE_TIMESTAMPS_MIGRATION_PATH))
    assert state_timestamps_migration["revision"] == "20260717_0007"
    assert state_timestamps_migration["down_revision"] == "20260717_0006"
    assert "ready_at IS NOT NULL" in state_timestamps_migration[
        "_STATUS_TIMESTAMPS_CHECK"
    ]


def test_stale_job_index_migration_preserves_jobs_across_direct_boundary(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "20260717_0005")
    engine, tables = _load_tables(postgres_url)
    try:
        with engine.begin() as connection:
            _seed_runtime_parent_chain(connection, tables)
            connection.execute(
                insert(tables["runtime_instances"]).values(**_instance_values())
            )
            connection.execute(
                insert(tables["runtime_lifecycle_jobs"]).values(**_job_values())
            )

        def index_names() -> set[str]:
            return {
                index["name"]
                for index in inspect(engine).get_indexes("runtime_lifecycle_jobs")
            }

        def persisted_job() -> tuple[str, str, int]:
            with engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT job_id, status, lease_generation "
                    "FROM public.runtime_lifecycle_jobs WHERE job_id = 'job-1'"
                ).one()
                return row[0], row[1], row[2]

        expected = ("job-1", "pending", 0)
        assert persisted_job() == expected
        assert "ix_runtime_job_stale_reconciliation" not in index_names()

        command.upgrade(config, "20260717_0006")
        assert persisted_job() == expected
        assert "ix_runtime_job_stale_reconciliation" in index_names()

        command.downgrade(config, "20260717_0005")
        assert persisted_job() == expected
        assert "ix_runtime_job_stale_reconciliation" not in index_names()

        command.upgrade(config, "head")
        assert persisted_job() == expected
        assert "ix_runtime_job_stale_reconciliation" in index_names()
    finally:
        engine.dispose()


def test_state_timestamp_migration_fails_closed_then_preserves_repaired_allocation(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "20260717_0006")
    engine, tables = _load_tables(postgres_url)
    try:
        with engine.begin() as connection:
            _seed_runtime_parent_chain(connection, tables)
            connection.execute(
                tables["state_allocations"]
                .update()
                .where(
                    tables["state_allocations"].c.state_allocation_id
                    == "state-allocation-1"
                )
                .values(status="ready", ready_at=None)
            )

        with pytest.raises(
            sqlalchemy.exc.DBAPIError,
            match="ck_state_allocations_status_timestamps",
        ):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260717_0006"

        with engine.begin() as connection:
            connection.execute(
                tables["state_allocations"]
                .update()
                .where(
                    tables["state_allocations"].c.state_allocation_id
                    == "state-allocation-1"
                )
                .values(ready_at=datetime(2026, 7, 17, tzinfo=UTC))
            )
        command.upgrade(config, "head")
        assert "ck_state_allocations_status_timestamps" in {
            check["name"]
            for check in inspect(engine).get_check_constraints("state_allocations")
        }

        command.downgrade(config, "20260717_0006")
        with engine.connect() as connection:
            row = connection.execute(
                select(
                    tables["state_allocations"].c.status,
                    tables["state_allocations"].c.ready_at,
                ).where(
                    tables["state_allocations"].c.state_allocation_id
                    == "state-allocation-1"
                )
            ).one()
        assert row.status == "ready"
        assert row.ready_at is not None
        assert "ck_state_allocations_status_timestamps" not in {
            check["name"]
            for check in inspect(engine).get_check_constraints("state_allocations")
        }
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "starting_revision",
    [
        "20260712_0001",
        "20260712_0002",
        "20260714_0003",
        "20260714_0004",
        "20260717_0005",
        "20260717_0006",
        "head",
    ],
)
def test_registration_migration_upgrades_supported_postgres_fixtures(
    postgres_url: str,
    starting_revision: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, starting_revision)
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            version = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            assert version == "20260717_0007"
    finally:
        engine.dispose()


def _registration_audit_values() -> dict[str, object]:
    return {
        "audit_event_id": "audit-register-phase2-spot-paper-probe",
        "actor_type": "operator_cli",
        "request_id": "request-register-phase2-spot-paper-probe",
        "idempotency_key": None,
        "owner_kind": "paper_probe",
        "owner_id": "phase2-spot-paper-probe",
        "owner_revision": "phase2-spot-paper-probe-v1",
        "instance_id": None,
        "runtime_spec_revision_id": None,
        "adapter_template_revision_id": None,
        "action": "register_paper_probe",
        "previous_state": None,
        "next_state": {"lifecycle_status": "registered"},
        "result_code": "registered",
        "occurred_at": datetime(2026, 7, 14, tzinfo=UTC),
        "provenance": {"source": "runtime_registration_repository"},
    }


def test_registration_migration_empty_downgrade_restores_0003_constraint(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260714_0003")
    engine = create_engine(postgres_url)
    metadata = MetaData()
    audit = Table("runtime_audit_events", metadata, autoload_with=engine)
    try:
        with engine.connect() as connection:
            version = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            assert version == "20260714_0003"
        _expect_integrity_error(engine, audit, _registration_audit_values())
    finally:
        engine.dispose()


def test_registration_migration_refuses_populated_downgrade_and_preserves_audit(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    metadata = MetaData()
    audit = Table("runtime_audit_events", metadata, autoload_with=engine)
    try:
        with engine.begin() as connection:
            connection.execute(insert(audit).values(**_registration_audit_values()))

        with pytest.raises(sqlalchemy.exc.DBAPIError, match="registration_audit_downgrade_refused"):
            command.downgrade(config, "20260714_0003")

        with engine.connect() as connection:
            version = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            assert version == "20260717_0007"
            assert connection.execute(
                select(audit.c.action).where(
                    audit.c.audit_event_id == "audit-register-phase2-spot-paper-probe"
                )
            ).scalar_one() == "register_paper_probe"
    finally:
        engine.dispose()
