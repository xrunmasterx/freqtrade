"""Allow immutable template publication actions in the shared audit log.

Revision ID: 20260714_0003
Revises: 20260712_0002
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260714_0003"
down_revision: str | None = "20260712_0002"
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
_LIFECYCLE_ACTION_CHECK = "action IN ('start', 'stop', 'retry', 'retire')"
_AUDIT_ACTION_CHECK = (
    "action IN ('start', 'stop', 'retry', 'retire', "
    "'publish_template', 'deprecate_template', 'revoke_template')"
)


def _control_migration_schema() -> None:
    op.execute(_CONTROLLED_SCHEMA_SQL)
    op.execute(_CONTROLLED_SCHEMA_GUARD_SQL)


def _replace_action_check(expression: str) -> None:
    op.drop_constraint(
        "ck_runtime_audit_events_action",
        "runtime_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_runtime_audit_events_action",
        "runtime_audit_events",
        expression,
    )


def upgrade() -> None:
    _control_migration_schema()
    _replace_action_check(_AUDIT_ACTION_CHECK)


def downgrade() -> None:
    _control_migration_schema()
    _replace_action_check(_LIFECYCLE_ACTION_CHECK)
