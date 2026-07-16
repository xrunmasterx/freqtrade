import importlib
import re
import runpy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import DateTime, Integer, MetaData, String, Text, create_engine, insert, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from freqtrade.platform.database import PlatformBase


BACKEND_ROOT = Path(__file__).parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic-platform.ini"
MIGRATION_PATH = (
    BACKEND_ROOT / "platform_migrations" / "versions" / "20260712_0002_templates_specs.py"
)
NOW = datetime(2026, 7, 13, 9, 30, tzinfo=UTC)

EXPECTED_COLUMNS = {
    "adapter_template_revisions": {
        "adapter_template_revision_id",
        "template_id",
        "semantic_version",
        "canonical_payload",
        "payload_digest",
        "source_commit",
        "root_commit",
        "backend_commit",
        "frontend_commit",
        "strategies_commit",
        "status",
        "published_by",
        "published_at",
        "deprecated_at",
        "revoked_at",
    },
    "state_allocations": {
        "state_allocation_id",
        "instance_id",
        "layout_id",
        "provider_id",
        "relative_path",
        "kind",
        "status",
        "generation",
        "restore_source_bundle_id",
        "created_at",
        "ready_at",
        "retired_at",
    },
    "secret_references": {
        "secret_reference_id",
        "provider_id",
        "secret_class",
        "logical_name",
        "owner_kind",
        "owner_id",
        "owner_revision",
        "status",
        "created_at",
        "retired_at",
    },
    "secret_version_metadata": {
        "secret_reference_id",
        "version_id",
        "status",
        "created_at",
        "activated_at",
        "retired_at",
    },
    "runtime_spec_revisions": {
        "runtime_spec_revision_id",
        "owner_kind",
        "owner_id",
        "owner_revision",
        "instance_kind",
        "catalog_revision_id",
        "environment",
        "adapter_template_revision_id",
        "state_allocation_id",
        "canonical_payload",
        "payload_digest",
        "created_at",
    },
}
EXPECTED_NULLABLE = {
    "adapter_template_revisions": {"deprecated_at", "revoked_at"},
    "state_allocations": {"restore_source_bundle_id", "ready_at", "retired_at"},
    "secret_references": {"retired_at"},
    "secret_version_metadata": {"retired_at"},
    "runtime_spec_revisions": set(),
}
EXPECTED_PRIMARY_KEYS = {
    "adapter_template_revisions": {"adapter_template_revision_id"},
    "state_allocations": {"state_allocation_id"},
    "secret_references": {"secret_reference_id"},
    "secret_version_metadata": {"secret_reference_id", "version_id"},
    "runtime_spec_revisions": {"runtime_spec_revision_id"},
}
EXPECTED_UNIQUES = {
    "adapter_template_revisions": {
        "uq_adapter_template_name_version",
        "uq_adapter_template_payload_digest",
    },
    "state_allocations": {"uq_state_allocation_relative_path"},
    "secret_references": {"uq_secret_reference_owner_logical_name"},
    "secret_version_metadata": set(),
    "runtime_spec_revisions": {"uq_runtime_spec_payload_digest"},
}
EXPECTED_CHECKS = {
    "adapter_template_revisions": {
        "ck_adapter_template_revisions_payload_digest_length",
        "ck_adapter_template_revisions_status",
    },
    "state_allocations": {
        "ck_state_allocations_generation",
        "ck_state_allocations_kind",
        "ck_state_allocations_provider_id",
        "ck_state_allocations_relative_path",
        "ck_state_allocations_status",
        "ck_state_allocations_status_timestamps",
    },
    "secret_references": {
        "ck_secret_references_owner_kind",
        "ck_secret_references_provider_id",
        "ck_secret_references_status",
    },
    "secret_version_metadata": {"ck_secret_version_metadata_status"},
    "runtime_spec_revisions": {
        "ck_runtime_spec_revisions_environment",
        "ck_runtime_spec_revisions_owner_kind",
        "ck_runtime_spec_revisions_payload_digest_hex",
        "ck_runtime_spec_revisions_payload_digest_length",
        "ck_runtime_spec_revisions_revision_id",
    },
}
PLACEHOLDER_FOREIGN_KEYS = {
    "fk_runtime_instances_runtime_spec_revision_id": (
        "runtime_instances",
        "runtime_spec_revision_id",
        "runtime_spec_revisions",
        "runtime_spec_revision_id",
    ),
    "fk_runtime_instances_state_allocation_id": (
        "runtime_instances",
        "state_allocation_id",
        "state_allocations",
        "state_allocation_id",
    ),
    "fk_runtime_attempts_runtime_spec_revision_id": (
        "runtime_attempts",
        "runtime_spec_revision_id",
        "runtime_spec_revisions",
        "runtime_spec_revision_id",
    ),
    "fk_runtime_attempts_adapter_template_revision_id": (
        "runtime_attempts",
        "adapter_template_revision_id",
        "adapter_template_revisions",
        "adapter_template_revision_id",
    ),
    "fk_runtime_audit_events_runtime_spec_revision_id": (
        "runtime_audit_events",
        "runtime_spec_revision_id",
        "runtime_spec_revisions",
        "runtime_spec_revision_id",
    ),
    "fk_runtime_audit_events_adapter_template_revision_id": (
        "runtime_audit_events",
        "adapter_template_revision_id",
        "adapter_template_revisions",
        "adapter_template_revision_id",
    ),
}
INTERNAL_FOREIGN_KEYS = {
    "secret_version_metadata": {
        "fk_secret_version_metadata_secret_reference_id": (
            ("secret_reference_id",),
            "secret_references",
            ("secret_reference_id",),
            "RESTRICT",
        ),
    },
    "runtime_spec_revisions": {
        "fk_runtime_spec_revisions_catalog_revision_id": (
            ("catalog_revision_id",),
            "platform_catalog_revisions",
            ("revision_id",),
            "RESTRICT",
        ),
        "fk_runtime_spec_revisions_adapter_template_revision_id": (
            ("adapter_template_revision_id",),
            "adapter_template_revisions",
            ("adapter_template_revision_id",),
            "RESTRICT",
        ),
        "fk_runtime_spec_revisions_state_allocation_id": (
            ("state_allocation_id",),
            "state_allocations",
            ("state_allocation_id",),
            "RESTRICT",
        ),
    },
}


def _alembic_config(postgres_url: str) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    return config


def _load_tables(postgres_url: str, table_names: set[str]):
    engine = create_engine(postgres_url)
    metadata = MetaData()
    metadata.reflect(bind=engine, only=table_names)
    return engine, {name: metadata.tables[name] for name in table_names}


def _expect_integrity_error(engine, table, values: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(insert(table).values(**values))


def _phase2a_instance_values(instance_id: str = "legacy-instance") -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "instance_kind": "execution-worker",
        "owner_kind": "paper_probe",
        "owner_id": "paper-probe-1",
        "owner_revision": "paper-probe-v1",
        "management_mode": "supervisor",
        "runtime_spec_revision_id": "legacy-missing-spec",
        "environment": "paper",
        "state_allocation_id": "legacy-missing-allocation",
        "desired_state": "stopped",
        "lifecycle_status": "registered",
        "failure_latched": False,
        "optimistic_version": 0,
        "created_at": NOW,
        "retired_at": None,
    }


def _phase2a_attempt_values() -> dict[str, object]:
    return {
        "attempt_id": "legacy-attempt",
        "instance_id": "legacy-instance",
        "attempt_number": 1,
        "runtime_spec_revision_id": "legacy-missing-spec",
        "adapter_template_revision_id": "legacy-missing-template",
        "resolved_secret_versions": {},
        "image_id": "sha256:legacy-image",
        "root_commit": "1" * 40,
        "backend_commit": "2" * 40,
        "frontend_commit": "3" * 40,
        "strategies_commit": "4" * 40,
        "project_identity": "legacy-project",
        "container_identity": "legacy-container",
        "status": "stopped",
        "health_result": None,
        "started_at": NOW,
        "stopped_at": NOW,
        "exit_code": 0,
        "failure_code": None,
    }


def _phase2a_audit_values() -> dict[str, object]:
    return {
        "audit_event_id": "legacy-audit",
        "actor_type": "migration",
        "request_id": "legacy-request",
        "idempotency_key": None,
        "owner_kind": "paper_probe",
        "owner_id": "paper-probe-1",
        "owner_revision": "paper-probe-v1",
        "instance_id": "legacy-instance",
        "runtime_spec_revision_id": "legacy-missing-spec",
        "adapter_template_revision_id": "legacy-missing-template",
        "action": "stop",
        "previous_state": {"status": "healthy"},
        "next_state": {"status": "stopped"},
        "result_code": "succeeded",
        "occurred_at": NOW,
        "provenance": {"source": "phase2a"},
    }


def _template_values(revision_id: str = "template-revision-1", **updates) -> dict[str, object]:
    values = {
        "adapter_template_revision_id": revision_id,
        "template_id": "freqtrade-bot",
        "semantic_version": "1.0.0",
        "canonical_payload": "{}",
        "payload_digest": "a" * 64,
        "source_commit": "1" * 40,
        "root_commit": "2" * 40,
        "backend_commit": "3" * 40,
        "frontend_commit": "4" * 40,
        "strategies_commit": "5" * 40,
        "status": "active",
        "published_by": "platform-admin",
        "published_at": NOW,
        "deprecated_at": None,
        "revoked_at": None,
    }
    values.update(updates)
    return values


def _allocation_values(allocation_id: str = "allocation-1", **updates) -> dict[str, object]:
    values = {
        "state_allocation_id": allocation_id,
        "instance_id": "instance-1",
        "layout_id": "freqtrade-userdata-v1",
        "provider_id": "managed-local-v1",
        "kind": "fresh",
        "status": "reserved",
        "generation": 1,
        "restore_source_bundle_id": None,
        "created_at": NOW,
        "ready_at": None,
        "retired_at": None,
    }
    values.update(updates)
    values.setdefault(
        "relative_path",
        f"ft_userdata/runtime/instances/{values['instance_id']}",
    )
    return values


def _secret_reference_values(reference_id: str = "secret-reference-1", **updates):
    values = {
        "secret_reference_id": reference_id,
        "provider_id": "local-file-v1",
        "secret_class": "exchange-api",
        "logical_name": "primary-exchange",
        "owner_kind": "paper_probe",
        "owner_id": "paper-probe-1",
        "owner_revision": "paper-probe-v1",
        "status": "active",
        "created_at": NOW,
        "retired_at": None,
    }
    values.update(updates)
    return values


def _runtime_spec_values(payload_digest: str = "b" * 64, **updates):
    values = {
        "runtime_spec_revision_id": f"runtime-spec-{payload_digest}",
        "owner_kind": "paper_probe",
        "owner_id": "paper-probe-1",
        "owner_revision": "paper-probe-v1",
        "instance_kind": "execution-worker",
        "catalog_revision_id": "catalog-revision-1",
        "environment": "paper",
        "adapter_template_revision_id": "template-revision-1",
        "state_allocation_id": "allocation-1",
        "canonical_payload": "{}",
        "payload_digest": payload_digest,
        "created_at": NOW,
    }
    values.update(updates)
    return values


def test_revision_chain_and_shared_metadata_define_the_exact_task1_schema() -> None:
    assert MIGRATION_PATH.is_file()
    revision = runpy.run_path(str(MIGRATION_PATH))
    assert revision["revision"] == "20260712_0002"
    assert revision["down_revision"] == "20260712_0001"

    models = importlib.import_module("freqtrade.platform.template_models")
    expected_record_names = {
        "AdapterTemplateRevisionRecord",
        "RuntimeSpecRevisionRecord",
        "SecretReferenceRecord",
        "SecretVersionMetadataRecord",
        "StateAllocationRecord",
    }
    assert all(hasattr(models, name) for name in expected_record_names)

    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        table = PlatformBase.metadata.tables[table_name]
        assert set(table.c.keys()) == expected_columns
        assert {column.name for column in table.primary_key.columns} == (
            EXPECTED_PRIMARY_KEYS[table_name]
        )
        assert {column.name for column in table.c if column.nullable} == (
            EXPECTED_NULLABLE[table_name]
        )
        assert {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        } == EXPECTED_UNIQUES[table_name]
        assert {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        } == EXPECTED_CHECKS[table_name]

    state_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in PlatformBase.metadata.tables["state_allocations"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    path_check = state_checks["ck_state_allocations_relative_path"]
    assert "instance_id <> ''" in path_check
    assert "replace(instance_id, '/', '') = instance_id" in path_check
    assert "replace(instance_id, '\\', '') = instance_id" in path_check
    assert "replace(instance_id, '.', '') = instance_id" in path_check
    assert (
        "relative_path = 'ft_userdata/runtime/instances/' || instance_id" in path_check
    )
    timestamp_check = state_checks["ck_state_allocations_status_timestamps"]
    assert "status = 'ready' AND ready_at IS NOT NULL AND retired_at IS NULL" in timestamp_check
    assert "status = 'retired' AND ready_at IS NULL AND retired_at IS NOT NULL" in timestamp_check

    runtime_spec_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in PlatformBase.metadata.tables["runtime_spec_revisions"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    digest_check = runtime_spec_checks["ck_runtime_spec_revisions_payload_digest_hex"]
    assert digest_check.count("replace(") == 16
    assert all(f"'{character}'" in digest_check for character in "0123456789abcdef")
    assert runtime_spec_checks["ck_runtime_spec_revisions_revision_id"] == (
        "runtime_spec_revision_id = 'runtime-spec-' || payload_digest"
    )

    assert isinstance(
        PlatformBase.metadata.tables["adapter_template_revisions"].c.canonical_payload.type,
        Text,
    )
    assert isinstance(
        PlatformBase.metadata.tables["runtime_spec_revisions"].c.canonical_payload.type,
        Text,
    )
    assert isinstance(PlatformBase.metadata.tables["state_allocations"].c.generation.type, Integer)
    for table_name, columns in (
        (
            "adapter_template_revisions",
            ("published_at", "deprecated_at", "revoked_at"),
        ),
        ("state_allocations", ("created_at", "ready_at", "retired_at")),
        ("secret_references", ("created_at", "retired_at")),
        ("secret_version_metadata", ("created_at", "activated_at", "retired_at")),
        ("runtime_spec_revisions", ("created_at",)),
    ):
        for column_name in columns:
            column_type = PlatformBase.metadata.tables[table_name].c[column_name].type
            assert isinstance(column_type, DateTime)
            assert column_type.timezone is True

    assert {
        index.name for index in PlatformBase.metadata.tables["state_allocations"].indexes
    } == {"uq_state_allocation_active"}
    assert {
        index.name for index in PlatformBase.metadata.tables["secret_version_metadata"].indexes
    } == {"uq_secret_version_active"}
    assert "state_allocations.instance_id" not in {
        f"{foreign_key.parent.table.name}.{foreign_key.parent.name}"
        for table in PlatformBase.metadata.tables.values()
        for foreign_key in table.foreign_keys
    }

    metadata_placeholder_fks = {
        constraint.name
        for table_name in ("runtime_instances", "runtime_attempts", "runtime_audit_events")
        for constraint in PlatformBase.metadata.tables[table_name].constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert set(PLACEHOLDER_FOREIGN_KEYS) <= metadata_placeholder_fks

    forbidden_tokens = (
        "secret_value",
        "secret_path",
        "host_path",
        "credential",
        "command",
        "capability",
        "privilege",
        "device",
        "compose",
    )
    assert not any(
        token in column_name
        for columns in EXPECTED_COLUMNS.values()
        for column_name in columns
        for token in forbidden_tokens
    )


def test_nonempty_0001_upgrade_preserves_identity_and_uses_not_valid_fks(
    postgres_url: str,
) -> None:
    config = _alembic_config(postgres_url)
    command.upgrade(config, "20260712_0001")
    phase2a_tables = {
        "runtime_instances",
        "runtime_attempts",
        "runtime_audit_events",
    }
    engine, tables = _load_tables(postgres_url, phase2a_tables)
    try:
        with engine.begin() as connection:
            connection.execute(insert(tables["runtime_instances"]).values(**_phase2a_instance_values()))
            connection.execute(insert(tables["runtime_attempts"]).values(**_phase2a_attempt_values()))
            connection.execute(insert(tables["runtime_audit_events"]).values(**_phase2a_audit_values()))
        with engine.connect() as connection:
            before = {
                name: connection.execute(table.select()).mappings().one()
                for name, table in tables.items()
            }

        command.upgrade(config, "20260712_0002")

        with engine.connect() as connection:
            after = {
                name: connection.execute(table.select()).mappings().one()
                for name, table in tables.items()
            }
            constraints = connection.exec_driver_sql(
                "SELECT constraint_row.conname, "
                "source_namespace.nspname AS source_schema, "
                "source_table.relname AS source_table, "
                "source_column.attname AS source_column, "
                "target_namespace.nspname AS target_schema, "
                "target_table.relname AS target_table, "
                "target_column.attname AS target_column, "
                "cardinality(constraint_row.conkey) AS source_column_count, "
                "cardinality(constraint_row.confkey) AS target_column_count, "
                "constraint_row.convalidated, constraint_row.confdeltype "
                "FROM pg_constraint AS constraint_row "
                "JOIN pg_class AS source_table "
                "ON source_table.oid = constraint_row.conrelid "
                "JOIN pg_namespace AS source_namespace "
                "ON source_namespace.oid = source_table.relnamespace "
                "JOIN pg_attribute AS source_column "
                "ON source_column.attrelid = source_table.oid "
                "AND source_column.attnum = constraint_row.conkey[1] "
                "JOIN pg_class AS target_table "
                "ON target_table.oid = constraint_row.confrelid "
                "JOIN pg_namespace AS target_namespace "
                "ON target_namespace.oid = target_table.relnamespace "
                "JOIN pg_attribute AS target_column "
                "ON target_column.attrelid = target_table.oid "
                "AND target_column.attnum = constraint_row.confkey[1] "
                "WHERE constraint_row.contype = 'f' AND constraint_row.conname IN ("
                + ",".join(f"'{name}'" for name in sorted(PLACEHOLDER_FOREIGN_KEYS))
                + ")"
            ).all()
        assert after == before
        actual_constraints = {
            row.conname: (
                row.source_schema,
                row.source_table,
                row.source_column,
                row.target_schema,
                row.target_table,
                row.target_column,
                row.source_column_count,
                row.target_column_count,
                row.convalidated,
                row.confdeltype,
            )
            for row in constraints
        }
        expected_constraints = {
            name: (
                "public",
                source_table,
                source_column,
                "public",
                target_table,
                target_column,
                1,
                1,
                False,
                "r",
            )
            for name, (
                source_table,
                source_column,
                target_table,
                target_column,
            ) in PLACEHOLDER_FOREIGN_KEYS.items()
        }
        assert actual_constraints == expected_constraints

        _expect_integrity_error(
            engine,
            tables["runtime_instances"],
            _phase2a_instance_values("new-invalid-instance"),
        )

        command.downgrade(config, "20260712_0001")
        with engine.connect() as connection:
            downgraded = {
                name: connection.execute(table.select()).mappings().one()
                for name, table in tables.items()
            }
            version = connection.exec_driver_sql(
                "SELECT version_num FROM public.alembic_version"
            ).scalar_one()
        assert downgraded == before
        assert version == "20260712_0001"
        assert set(EXPECTED_COLUMNS).isdisjoint(inspect(engine).get_table_names(schema="public"))
    finally:
        engine.dispose()


def test_postgres_schema_has_exact_lengths_constraints_indexes_and_restrictive_fks(
    postgres_url: str,
) -> None:
    command.upgrade(_alembic_config(postgres_url), "head")
    schema = inspect(create_engine(postgres_url))
    try:
        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            columns = {column["name"]: column for column in schema.get_columns(table_name)}
            assert set(columns) == expected_columns
            assert {name for name, column in columns.items() if column["nullable"]} == (
                EXPECTED_NULLABLE[table_name]
            )
            assert {check["name"] for check in schema.get_check_constraints(table_name)} == (
                EXPECTED_CHECKS[table_name]
            )
            assert {
                unique["name"] for unique in schema.get_unique_constraints(table_name)
            } == EXPECTED_UNIQUES[table_name]
            assert set(schema.get_pk_constraint(table_name)["constrained_columns"]) == (
                EXPECTED_PRIMARY_KEYS[table_name]
            )

        expected_lengths = {
            "adapter_template_revisions": {
                "adapter_template_revision_id": 128,
                "template_id": 128,
                "semantic_version": 64,
                "payload_digest": 64,
                "source_commit": 64,
                "root_commit": 64,
                "backend_commit": 64,
                "frontend_commit": 64,
                "strategies_commit": 64,
                "status": 16,
                "published_by": 128,
            },
            "state_allocations": {
                "state_allocation_id": 128,
                "instance_id": 128,
                "layout_id": 128,
                "provider_id": 128,
                "relative_path": 256,
                "kind": 32,
                "status": 32,
                "restore_source_bundle_id": 128,
            },
            "secret_references": {
                "secret_reference_id": 128,
                "provider_id": 128,
                "secret_class": 128,
                "logical_name": 128,
                "owner_kind": 128,
                "owner_id": 128,
                "owner_revision": 128,
                "status": 32,
            },
            "secret_version_metadata": {
                "secret_reference_id": 128,
                "version_id": 128,
                "status": 32,
            },
            "runtime_spec_revisions": {
                "runtime_spec_revision_id": 128,
                "owner_kind": 128,
                "owner_id": 128,
                "owner_revision": 128,
                "instance_kind": 128,
                "catalog_revision_id": 128,
                "environment": 16,
                "adapter_template_revision_id": 128,
                "state_allocation_id": 128,
                "payload_digest": 64,
            },
        }
        for table_name, lengths in expected_lengths.items():
            columns = {column["name"]: column for column in schema.get_columns(table_name)}
            for column_name, length in lengths.items():
                assert isinstance(columns[column_name]["type"], String)
                assert columns[column_name]["type"].length == length
        assert isinstance(
            {column["name"]: column for column in schema.get_columns(
                "adapter_template_revisions"
            )}["canonical_payload"]["type"],
            Text,
        )
        assert isinstance(
            {column["name"]: column for column in schema.get_columns("runtime_spec_revisions")}[
                "canonical_payload"
            ]["type"],
            Text,
        )
        assert isinstance(
            {column["name"]: column for column in schema.get_columns("state_allocations")}[
                "generation"
            ]["type"],
            Integer,
        )
        for table_name, column_names in (
            (
                "adapter_template_revisions",
                ("published_at", "deprecated_at", "revoked_at"),
            ),
            ("state_allocations", ("created_at", "ready_at", "retired_at")),
            ("secret_references", ("created_at", "retired_at")),
            ("secret_version_metadata", ("created_at", "activated_at", "retired_at")),
            ("runtime_spec_revisions", ("created_at",)),
        ):
            columns = {column["name"]: column for column in schema.get_columns(table_name)}
            for column_name in column_names:
                assert isinstance(columns[column_name]["type"], DateTime)
                assert columns[column_name]["type"].timezone is True

        state_indexes = {index["name"]: index for index in schema.get_indexes("state_allocations")}
        secret_indexes = {
            index["name"]: index for index in schema.get_indexes("secret_version_metadata")
        }
        assert state_indexes["uq_state_allocation_active"]["unique"] is True
        assert secret_indexes["uq_secret_version_active"]["unique"] is True
        state_predicate = state_indexes["uq_state_allocation_active"]["dialect_options"][
            "postgresql_where"
        ]
        secret_predicate = secret_indexes["uq_secret_version_active"]["dialect_options"][
            "postgresql_where"
        ]
        assert set(re.findall(r"'([a-z_]+)'", state_predicate)) == {
            "reserved",
            "provisioning",
            "ready",
        }
        assert set(re.findall(r"'([a-z_]+)'", secret_predicate)) == {"active"}

        for table_name, expected_foreign_keys in INTERNAL_FOREIGN_KEYS.items():
            actual = {
                foreign_key["name"]: (
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    tuple(foreign_key["referred_columns"]),
                    foreign_key["options"].get("ondelete"),
                )
                for foreign_key in schema.get_foreign_keys(table_name)
            }
            assert actual == expected_foreign_keys
        assert schema.get_foreign_keys("state_allocations") == []

        internal_names = {
            name for foreign_keys in INTERNAL_FOREIGN_KEYS.values() for name in foreign_keys
        }
        with schema.bind.connect() as connection:
            internal_constraints = connection.exec_driver_sql(
                "SELECT conname, convalidated, confdeltype FROM pg_constraint "
                "WHERE conname IN ("
                + ",".join(f"'{name}'" for name in sorted(internal_names))
                + ")"
            ).all()
        assert {
            row.conname: (row.convalidated, row.confdeltype) for row in internal_constraints
        } == {name: (True, "r") for name in internal_names}
    finally:
        schema.bind.dispose()


def test_postgres_rejects_conflicting_digests_closed_values_and_multiple_active_rows(
    postgres_url: str,
) -> None:
    command.upgrade(_alembic_config(postgres_url), "head")
    task1_tables = set(EXPECTED_COLUMNS) | {"platform_catalog_revisions"}
    engine, tables = _load_tables(postgres_url, task1_tables)
    try:
        with engine.begin() as connection:
            connection.execute(insert(tables["adapter_template_revisions"]).values(**_template_values()))
            connection.execute(insert(tables["state_allocations"]).values(**_allocation_values()))
            connection.execute(
                insert(tables["secret_references"]).values(**_secret_reference_values())
            )
            connection.execute(
                insert(tables["secret_version_metadata"]).values(
                    secret_reference_id="secret-reference-1",
                    version_id="version-1",
                    status="active",
                    created_at=NOW,
                    activated_at=NOW,
                    retired_at=None,
                )
            )
            connection.execute(
                insert(tables["platform_catalog_revisions"]).values(
                    revision_id="catalog-revision-1",
                    payload={},
                    created_at=NOW,
                )
            )
            connection.execute(insert(tables["runtime_spec_revisions"]).values(**_runtime_spec_values()))

        _expect_integrity_error(
            engine,
            tables["adapter_template_revisions"],
            _template_values("template-conflict", payload_digest="c" * 64),
        )
        _expect_integrity_error(
            engine,
            tables["adapter_template_revisions"],
            _template_values(
                "template-duplicate-digest",
                template_id="other-template",
                semantic_version="2.0.0",
            ),
        )
        _expect_integrity_error(
            engine,
            tables["state_allocations"],
            _allocation_values("allocation-2", status="ready", ready_at=NOW),
        )
        _expect_integrity_error(
            engine,
            tables["state_allocations"],
            _allocation_values(
                "allocation-retired",
                status="retired",
                retired_at=NOW,
            ),
        )
        with engine.begin() as connection:
            connection.execute(
                insert(tables["state_allocations"]).values(
                    **_allocation_values(
                        "allocation-other-instance",
                        instance_id="other-instance",
                        status="retired",
                        retired_at=NOW,
                    )
                )
            )
        _expect_integrity_error(
            engine,
            tables["secret_version_metadata"],
            {
                "secret_reference_id": "secret-reference-1",
                "version_id": "version-2",
                "status": "active",
                "created_at": NOW,
                "activated_at": NOW,
                "retired_at": None,
            },
        )
        with engine.begin() as connection:
            connection.execute(
                insert(tables["secret_version_metadata"]).values(
                    secret_reference_id="secret-reference-1",
                    version_id="version-retired",
                    status="retired",
                    created_at=NOW,
                    activated_at=NOW,
                    retired_at=NOW,
                )
            )
        _expect_integrity_error(
            engine,
            tables["runtime_spec_revisions"],
            _runtime_spec_values(),
        )
        _expect_integrity_error(
            engine,
            tables["secret_references"],
            _secret_reference_values("secret-reference-duplicate"),
        )

        invalid_rows = (
            (
                "adapter_template_revisions",
                _template_values(
                    "bad-template-status",
                    template_id="bad-template-status",
                    semantic_version="2.0.0",
                    status="draft",
                    payload_digest="d" * 64,
                ),
            ),
            (
                "adapter_template_revisions",
                _template_values(
                    "bad-template-digest",
                    template_id="bad-template-digest",
                    semantic_version="2.0.0",
                    payload_digest="short",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-state-provider",
                    instance_id="bad-state-provider",
                    provider_id="host",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-state-kind",
                    instance_id="bad-state-kind",
                    kind="caller-path",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-state-status",
                    instance_id="bad-state-status",
                    status="deleted",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-ready-without-ready-at",
                    instance_id="bad-ready-without-ready-at",
                    status="ready",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-reserved-with-ready-at",
                    instance_id="bad-reserved-with-ready-at",
                    status="reserved",
                    ready_at=NOW,
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-provisioning-with-retired-at",
                    instance_id="bad-provisioning-with-retired-at",
                    status="provisioning",
                    retired_at=NOW,
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-quarantined-with-ready-at",
                    instance_id="bad-quarantined-with-ready-at",
                    status="quarantined",
                    ready_at=NOW,
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-retired-without-retired-at",
                    instance_id="bad-retired-without-retired-at",
                    status="retired",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-generation",
                    instance_id="bad-generation",
                    generation=0,
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-path-mismatch",
                    instance_id="bad-path-mismatch",
                    relative_path="ft_userdata/runtime/instances/mismatched-instance",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-path-absolute",
                    instance_id="bad-path-absolute",
                    relative_path="/absolute/runtime-state",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-instance-empty",
                    instance_id="",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-instance-forward-slash",
                    instance_id="nested/instance",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-instance-backslash",
                    instance_id=r"nested\instance",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-instance-dot",
                    instance_id="instance.with-dot",
                ),
            ),
            (
                "state_allocations",
                _allocation_values(
                    "bad-path-traversal",
                    instance_id="../escape",
                ),
            ),
            (
                "secret_references",
                _secret_reference_values(
                    "bad-secret-provider",
                    provider_id="environment",
                    logical_name="bad-secret-provider",
                ),
            ),
            (
                "secret_references",
                _secret_reference_values(
                    "bad-secret-owner",
                    owner_kind="operator",
                    logical_name="bad-secret-owner",
                ),
            ),
            (
                "secret_references",
                _secret_reference_values(
                    "bad-secret-status",
                    status="deleted",
                    logical_name="bad-secret-status",
                ),
            ),
            (
                "secret_version_metadata",
                {
                    "secret_reference_id": "secret-reference-1",
                    "version_id": "bad-secret-version-status",
                    "status": "pending",
                    "created_at": NOW,
                    "activated_at": NOW,
                    "retired_at": None,
                },
            ),
            (
                "runtime_spec_revisions",
                _runtime_spec_values(
                    "e" * 64,
                    environment="simulation",
                ),
            ),
            (
                "runtime_spec_revisions",
                _runtime_spec_values(
                    "f" * 64,
                    owner_kind="operator",
                ),
            ),
            (
                "runtime_spec_revisions",
                _runtime_spec_values("short"),
            ),
            (
                "runtime_spec_revisions",
                _runtime_spec_values("g" * 64),
            ),
            (
                "runtime_spec_revisions",
                _runtime_spec_values(
                    "c" * 64,
                    runtime_spec_revision_id="runtime-spec-wrong-id",
                ),
            ),
        )
        for table_name, values in invalid_rows:
            _expect_integrity_error(engine, tables[table_name], values)
    finally:
        engine.dispose()
