"""Create the initial runtime registry schema.

Revision ID: 20260712_0001
Revises:
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260712_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CATALOG_ADOPTION_SQL = r"""
DO $catalog_adoption$
DECLARE
    catalog_oid oid;
    revision_attnum smallint;
    compatible boolean;
BEGIN
    catalog_oid := to_regclass('public.platform_catalog_revisions');
    IF catalog_oid IS NULL THEN
        CREATE TABLE platform_catalog_revisions (
            revision_id varchar(128) NOT NULL,
            payload json NOT NULL,
            created_at timestamp with time zone NOT NULL,
            PRIMARY KEY (revision_id)
        );
        RETURN;
    END IF;

    SELECT
        relation.relkind = 'r'
        AND relation.relpersistence = 'p'
        AND NOT relation.relispartition
    INTO compatible
    FROM pg_class AS relation
    WHERE relation.oid = catalog_oid;
    IF NOT compatible THEN
        RAISE EXCEPTION 'incompatible_platform_catalog_revisions';
    END IF;

    SELECT
        count(*) = 3
        AND count(*) FILTER (
            WHERE attribute.attname = 'revision_id'
              AND format_type(attribute.atttypid, attribute.atttypmod)
                  = 'character varying(128)'
              AND attribute.attnotnull
              AND NOT attribute.atthasdef
              AND attribute.attidentity = ''
              AND attribute.attgenerated = ''
        ) = 1
        AND count(*) FILTER (
            WHERE attribute.attname = 'payload'
              AND format_type(attribute.atttypid, attribute.atttypmod) = 'json'
              AND attribute.attnotnull
              AND NOT attribute.atthasdef
              AND attribute.attidentity = ''
              AND attribute.attgenerated = ''
        ) = 1
        AND count(*) FILTER (
            WHERE attribute.attname = 'created_at'
              AND format_type(attribute.atttypid, attribute.atttypmod)
                  = 'timestamp with time zone'
              AND attribute.attnotnull
              AND NOT attribute.atthasdef
              AND attribute.attidentity = ''
              AND attribute.attgenerated = ''
        ) = 1
    INTO compatible
    FROM pg_attribute AS attribute
    WHERE attribute.attrelid = catalog_oid
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped;
    IF NOT compatible THEN
        RAISE EXCEPTION 'incompatible_platform_catalog_revisions';
    END IF;

    SELECT attribute.attnum
    INTO revision_attnum
    FROM pg_attribute AS attribute
    WHERE attribute.attrelid = catalog_oid
      AND attribute.attname = 'revision_id'
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped;

    SELECT
        count(*) = 1
        AND count(*) FILTER (
            WHERE constraint_row.contype = 'p'
              AND constraint_row.conkey = ARRAY[revision_attnum]::smallint[]
              AND constraint_row.convalidated
              AND NOT constraint_row.condeferrable
        ) = 1
    INTO compatible
    FROM pg_constraint AS constraint_row
    WHERE constraint_row.conrelid = catalog_oid;
    IF NOT compatible THEN
        RAISE EXCEPTION 'incompatible_platform_catalog_revisions';
    END IF;

    SELECT
        count(*) = 1
        AND count(*) FILTER (
            WHERE index_row.indisprimary
              AND index_row.indisunique
              AND index_row.indisvalid
              AND index_row.indisready
              AND index_row.indnatts = 1
              AND index_row.indnkeyatts = 1
              AND index_row.indkey[0] = revision_attnum
              AND index_row.indexprs IS NULL
              AND index_row.indpred IS NULL
        ) = 1
    INTO compatible
    FROM pg_index AS index_row
    WHERE index_row.indrelid = catalog_oid;
    IF NOT compatible THEN
        RAISE EXCEPTION 'incompatible_platform_catalog_revisions';
    END IF;
END
$catalog_adoption$
"""


def _adopt_or_create_catalog_table() -> None:
    op.execute(_CATALOG_ADOPTION_SQL)


def upgrade() -> None:
    _adopt_or_create_catalog_table()
    op.create_table(
        "runtime_instances",
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("instance_kind", sa.String(length=128), nullable=False),
        sa.Column("owner_kind", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("owner_revision", sa.String(length=128), nullable=False),
        sa.Column("management_mode", sa.String(length=128), nullable=False),
        sa.Column("runtime_spec_revision_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("state_allocation_id", sa.String(length=128), nullable=False),
        sa.Column("desired_state", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=32), nullable=False),
        sa.Column("failure_latched", sa.Boolean(), nullable=False),
        sa.Column("optimistic_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "owner_kind IN ('migration_bot', 'paper_probe', 'workspace_worker')",
            name="ck_runtime_instances_owner_kind",
        ),
        sa.CheckConstraint(
            "management_mode = 'supervisor'",
            name="ck_runtime_instances_management_mode",
        ),
        sa.CheckConstraint(
            "environment IN ('paper', 'live')",
            name="ck_runtime_instances_environment",
        ),
        sa.CheckConstraint(
            "desired_state IN ('stopped', 'running', 'retired')",
            name="ck_runtime_instances_desired_state",
        ),
        sa.CheckConstraint(
            "lifecycle_status IN "
            "('registered', 'provisioning', 'stopped', 'starting', 'healthy', "
            "'stopping', 'failed', 'retired')",
            name="ck_runtime_instances_lifecycle_status",
        ),
        sa.CheckConstraint(
            "optimistic_version >= 0",
            name="ck_runtime_instances_optimistic_version",
        ),
        sa.PrimaryKeyConstraint("instance_id"),
    )
    op.create_table(
        "runtime_attempts",
        sa.Column("attempt_id", sa.String(length=128), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("runtime_spec_revision_id", sa.String(length=128), nullable=False),
        sa.Column("adapter_template_revision_id", sa.String(length=128), nullable=False),
        sa.Column("resolved_secret_versions", sa.JSON(), nullable=False),
        sa.Column("image_id", sa.String(length=256), nullable=False),
        sa.Column("root_commit", sa.String(length=64), nullable=False),
        sa.Column("backend_commit", sa.String(length=64), nullable=False),
        sa.Column("frontend_commit", sa.String(length=64), nullable=False),
        sa.Column("strategies_commit", sa.String(length=64), nullable=False),
        sa.Column("project_identity", sa.String(length=128), nullable=False),
        sa.Column("container_identity", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("health_result", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_runtime_attempts_attempt_number",
        ),
        sa.CheckConstraint(
            "status IN "
            "('pending', 'validating', 'launching', 'healthy', 'stopping', 'stopped', 'failed')",
            name="ck_runtime_attempts_status",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["runtime_instances.instance_id"],
            name="fk_runtime_attempts_instance_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint(
            "instance_id",
            "attempt_number",
            name="uq_runtime_attempt_instance_number",
        ),
    )
    op.create_index(
        "uq_runtime_attempt_active",
        "runtime_attempts",
        ["instance_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'validating', 'launching', 'healthy', 'stopping')"
        ),
    )
    op.create_table(
        "runtime_lifecycle_jobs",
        sa.Column("job_id", sa.String(length=128), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("requested_action", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("expected_instance_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "requested_action IN ('start', 'stop', 'retry', 'retire')",
            name="ck_runtime_lifecycle_jobs_requested_action",
        ),
        sa.CheckConstraint(
            "expected_instance_version >= 0",
            name="ck_runtime_lifecycle_jobs_expected_instance_version",
        ),
        sa.CheckConstraint(
            "status IN "
            "('pending', 'claimed', 'running', 'succeeded', 'failed', 'needs_reconciliation')",
            name="ck_runtime_lifecycle_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["runtime_instances.instance_id"],
            name="fk_runtime_lifecycle_jobs_instance_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint(
            "instance_id",
            "idempotency_key",
            name="uq_runtime_job_instance_idempotency",
        ),
    )
    op.create_index(
        "uq_runtime_job_active",
        "runtime_lifecycle_jobs",
        ["instance_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'claimed', 'running')"),
    )
    op.create_table(
        "runtime_endpoints",
        sa.Column("endpoint_id", sa.String(length=128), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("attempt_id", sa.String(length=128), nullable=False),
        sa.Column("endpoint_kind", sa.String(length=128), nullable=False),
        sa.Column("internal_port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False),
        sa.Column("exposure_policy", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "internal_port BETWEEN 1 AND 65535",
            name="ck_runtime_endpoints_internal_port",
        ),
        sa.CheckConstraint(
            "protocol IN ('http', 'https')",
            name="ck_runtime_endpoints_protocol",
        ),
        sa.CheckConstraint(
            "exposure_policy IN ('internal_only', 'none')",
            name="ck_runtime_endpoints_exposure_policy",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["runtime_attempts.attempt_id"],
            name="fk_runtime_endpoints_attempt_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["runtime_instances.instance_id"],
            name="fk_runtime_endpoints_instance_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("endpoint_id"),
        sa.UniqueConstraint(
            "attempt_id",
            "endpoint_kind",
            name="uq_runtime_endpoint_attempt_kind",
        ),
    )
    op.create_table(
        "runtime_access_requests",
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("attempt_id", sa.String(length=128), nullable=False),
        sa.Column("route_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_code", sa.String(length=128), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["runtime_attempts.attempt_id"],
            name="fk_runtime_access_requests_attempt_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["runtime_instances.instance_id"],
            name="fk_runtime_access_requests_instance_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_table(
        "runtime_audit_events",
        sa.Column("audit_event_id", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("owner_kind", sa.String(length=128), nullable=True),
        sa.Column("owner_id", sa.String(length=128), nullable=True),
        sa.Column("owner_revision", sa.String(length=128), nullable=True),
        sa.Column("instance_id", sa.String(length=128), nullable=True),
        sa.Column("runtime_spec_revision_id", sa.String(length=128), nullable=True),
        sa.Column("adapter_template_revision_id", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("previous_state", sa.JSON(), nullable=True),
        sa.Column("next_state", sa.JSON(), nullable=True),
        sa.Column("result_code", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "owner_kind IN ('migration_bot', 'paper_probe', 'workspace_worker')",
            name="ck_runtime_audit_events_owner_kind",
        ),
        sa.CheckConstraint(
            "action IN ('start', 'stop', 'retry', 'retire')",
            name="ck_runtime_audit_events_action",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["runtime_instances.instance_id"],
            name="fk_runtime_audit_events_instance_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("audit_event_id"),
    )


def downgrade() -> None:
    op.drop_table("runtime_audit_events")
    op.drop_table("runtime_access_requests")
    op.drop_table("runtime_endpoints")
    op.drop_index("uq_runtime_job_active", table_name="runtime_lifecycle_jobs")
    op.drop_table("runtime_lifecycle_jobs")
    op.drop_index("uq_runtime_attempt_active", table_name="runtime_attempts")
    op.drop_table("runtime_attempts")
    op.drop_table("runtime_instances")
    op.drop_table("platform_catalog_revisions")
