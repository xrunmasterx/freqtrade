"""Index stale reconciliation jobs used by the Supervisor claim loop.

Revision ID: 20260717_0006
Revises: 20260717_0005
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260717_0006"
down_revision: str | None = "20260717_0005"
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


def _control_migration_schema() -> None:
    op.execute(_CONTROLLED_SCHEMA_SQL)
    op.execute(_CONTROLLED_SCHEMA_GUARD_SQL)


def upgrade() -> None:
    _control_migration_schema()
    op.create_index(
        "ix_runtime_job_stale_reconciliation",
        "runtime_lifecycle_jobs",
        ("status", "failure_code", "completed_at", "job_id"),
        unique=False,
    )


def downgrade() -> None:
    _control_migration_schema()
    op.drop_index(
        "ix_runtime_job_stale_reconciliation",
        table_name="runtime_lifecycle_jobs",
    )
