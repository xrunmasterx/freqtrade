"""Constrain State Allocation status and lifecycle timestamps.

Revision ID: 20260717_0007
Revises: 20260717_0006
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260717_0007"
down_revision: str | None = "20260717_0006"
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
_STATUS_TIMESTAMPS_CHECK = (
    "(status = 'ready' AND ready_at IS NOT NULL AND retired_at IS NULL) OR "
    "(status IN ('reserved', 'provisioning', 'quarantined') "
    "AND ready_at IS NULL AND retired_at IS NULL) OR "
    "(status = 'retired' AND ready_at IS NULL AND retired_at IS NOT NULL)"
)


def _control_migration_schema() -> None:
    op.execute(_CONTROLLED_SCHEMA_SQL)
    op.execute(_CONTROLLED_SCHEMA_GUARD_SQL)


def upgrade() -> None:
    _control_migration_schema()
    op.create_check_constraint(
        "ck_state_allocations_status_timestamps",
        "state_allocations",
        _STATUS_TIMESTAMPS_CHECK,
    )


def downgrade() -> None:
    _control_migration_schema()
    op.drop_constraint(
        "ck_state_allocations_status_timestamps",
        "state_allocations",
        type_="check",
    )
