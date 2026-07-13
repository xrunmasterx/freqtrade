"""Add trusted adapter templates and runtime specification contracts.

Revision ID: 20260712_0002
Revises: 20260712_0001
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260712_0002"
down_revision: str | None = "20260712_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONTROLLED_SCHEMA_SQL = r"""
SET LOCAL search_path TO public, pg_catalog
"""
_CONTROLLED_SCHEMA_GUARD_SQL = r"""
DO $controlled_schema$
BEGIN
    IF current_setting('search_path') <> 'public, pg_catalog'
       OR to_regnamespace('public') IS NULL THEN
        RAISE EXCEPTION 'platform_migration_schema_control_failed';
    END IF;
END
$controlled_schema$
"""

_PLACEHOLDER_FOREIGN_KEYS = (
    (
        "runtime_instances",
        "fk_runtime_instances_runtime_spec_revision_id",
        "runtime_spec_revision_id",
        "runtime_spec_revisions",
        "runtime_spec_revision_id",
    ),
    (
        "runtime_instances",
        "fk_runtime_instances_state_allocation_id",
        "state_allocation_id",
        "state_allocations",
        "state_allocation_id",
    ),
    (
        "runtime_attempts",
        "fk_runtime_attempts_runtime_spec_revision_id",
        "runtime_spec_revision_id",
        "runtime_spec_revisions",
        "runtime_spec_revision_id",
    ),
    (
        "runtime_attempts",
        "fk_runtime_attempts_adapter_template_revision_id",
        "adapter_template_revision_id",
        "adapter_template_revisions",
        "adapter_template_revision_id",
    ),
    (
        "runtime_audit_events",
        "fk_runtime_audit_events_runtime_spec_revision_id",
        "runtime_spec_revision_id",
        "runtime_spec_revisions",
        "runtime_spec_revision_id",
    ),
    (
        "runtime_audit_events",
        "fk_runtime_audit_events_adapter_template_revision_id",
        "adapter_template_revision_id",
        "adapter_template_revisions",
        "adapter_template_revision_id",
    ),
)


def _control_migration_schema() -> None:
    op.execute(_CONTROLLED_SCHEMA_SQL)
    op.execute(_CONTROLLED_SCHEMA_GUARD_SQL)


def upgrade() -> None:
    _control_migration_schema()
    op.create_table(
        "adapter_template_revisions",
        sa.Column("adapter_template_revision_id", sa.String(length=128), nullable=False),
        sa.Column("template_id", sa.String(length=128), nullable=False),
        sa.Column("semantic_version", sa.String(length=64), nullable=False),
        sa.Column("canonical_payload", sa.Text(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("source_commit", sa.String(length=64), nullable=False),
        sa.Column("root_commit", sa.String(length=64), nullable=False),
        sa.Column("backend_commit", sa.String(length=64), nullable=False),
        sa.Column("frontend_commit", sa.String(length=64), nullable=False),
        sa.Column("strategies_commit", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("published_by", sa.String(length=128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'deprecated', 'revoked')",
            name="ck_adapter_template_revisions_status",
        ),
        sa.CheckConstraint(
            "length(payload_digest) = 64",
            name="ck_adapter_template_revisions_payload_digest_length",
        ),
        sa.PrimaryKeyConstraint("adapter_template_revision_id"),
        sa.UniqueConstraint(
            "template_id",
            "semantic_version",
            name="uq_adapter_template_name_version",
        ),
        sa.UniqueConstraint(
            "payload_digest",
            name="uq_adapter_template_payload_digest",
        ),
    )
    op.create_table(
        "state_allocations",
        sa.Column("state_allocation_id", sa.String(length=128), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("layout_id", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("relative_path", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("restore_source_bundle_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "provider_id = 'managed-local-v1'",
            name="ck_state_allocations_provider_id",
        ),
        sa.CheckConstraint(
            "kind IN ('fresh', 'restored')",
            name="ck_state_allocations_kind",
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'provisioning', 'ready', 'quarantined', 'retired')",
            name="ck_state_allocations_status",
        ),
        sa.CheckConstraint(
            "generation >= 1",
            name="ck_state_allocations_generation",
        ),
        sa.PrimaryKeyConstraint("state_allocation_id"),
        sa.UniqueConstraint(
            "relative_path",
            name="uq_state_allocation_relative_path",
        ),
    )
    op.create_index(
        "uq_state_allocation_active",
        "state_allocations",
        ["instance_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('reserved', 'provisioning', 'ready')"),
    )
    op.create_table(
        "secret_references",
        sa.Column("secret_reference_id", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("secret_class", sa.String(length=128), nullable=False),
        sa.Column("logical_name", sa.String(length=128), nullable=False),
        sa.Column("owner_kind", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("owner_revision", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "provider_id = 'local-file-v1'",
            name="ck_secret_references_provider_id",
        ),
        sa.CheckConstraint(
            "owner_kind IN ('migration_bot', 'paper_probe', 'workspace_worker')",
            name="ck_secret_references_owner_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'retired')",
            name="ck_secret_references_status",
        ),
        sa.PrimaryKeyConstraint("secret_reference_id"),
        sa.UniqueConstraint(
            "owner_kind",
            "owner_id",
            "owner_revision",
            "logical_name",
            name="uq_secret_reference_owner_logical_name",
        ),
    )
    op.create_table(
        "secret_version_metadata",
        sa.Column("secret_reference_id", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_secret_version_metadata_status",
        ),
        sa.ForeignKeyConstraint(
            ["secret_reference_id"],
            ["secret_references.secret_reference_id"],
            name="fk_secret_version_metadata_secret_reference_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("secret_reference_id", "version_id"),
    )
    op.create_index(
        "uq_secret_version_active",
        "secret_version_metadata",
        ["secret_reference_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "runtime_spec_revisions",
        sa.Column("runtime_spec_revision_id", sa.String(length=128), nullable=False),
        sa.Column("owner_kind", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("owner_revision", sa.String(length=128), nullable=False),
        sa.Column("instance_kind", sa.String(length=128), nullable=False),
        sa.Column("catalog_revision_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("adapter_template_revision_id", sa.String(length=128), nullable=False),
        sa.Column("state_allocation_id", sa.String(length=128), nullable=False),
        sa.Column("canonical_payload", sa.Text(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "owner_kind IN ('migration_bot', 'paper_probe', 'workspace_worker')",
            name="ck_runtime_spec_revisions_owner_kind",
        ),
        sa.CheckConstraint(
            "environment IN ('paper', 'live')",
            name="ck_runtime_spec_revisions_environment",
        ),
        sa.CheckConstraint(
            "length(payload_digest) = 64",
            name="ck_runtime_spec_revisions_payload_digest_length",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_revision_id"],
            ["platform_catalog_revisions.revision_id"],
            name="fk_runtime_spec_revisions_catalog_revision_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["adapter_template_revision_id"],
            ["adapter_template_revisions.adapter_template_revision_id"],
            name="fk_runtime_spec_revisions_adapter_template_revision_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["state_allocation_id"],
            ["state_allocations.state_allocation_id"],
            name="fk_runtime_spec_revisions_state_allocation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("runtime_spec_revision_id"),
        sa.UniqueConstraint(
            "payload_digest",
            name="uq_runtime_spec_payload_digest",
        ),
    )

    for table, name, column, target_table, target_column in _PLACEHOLDER_FOREIGN_KEYS:
        op.execute(
            f"ALTER TABLE public.{table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES public.{target_table} ({target_column}) "
            "ON DELETE RESTRICT NOT VALID"
        )


def downgrade() -> None:
    _control_migration_schema()
    for table, name, _column, _target_table, _target_column in reversed(
        _PLACEHOLDER_FOREIGN_KEYS
    ):
        op.drop_constraint(name, table, type_="foreignkey")

    op.drop_table("runtime_spec_revisions")
    op.drop_index("uq_secret_version_active", table_name="secret_version_metadata")
    op.drop_table("secret_version_metadata")
    op.drop_table("secret_references")
    op.drop_index("uq_state_allocation_active", table_name="state_allocations")
    op.drop_table("state_allocations")
    op.drop_table("adapter_template_revisions")
