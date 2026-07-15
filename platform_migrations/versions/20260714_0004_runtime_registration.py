"""Allow the fixed paper-probe registration action in the shared audit log.

Revision ID: 20260714_0004
Revises: 20260714_0003
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260714_0004"
down_revision: str | None = "20260714_0003"
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
_TEMPLATE_AUDIT_ACTION_CHECK = (
    "action IN ('start', 'stop', 'retry', 'retire', "
    "'publish_template', 'deprecate_template', 'revoke_template')"
)
_REGISTRATION_AUDIT_ACTION_CHECK = (
    "action IN ('start', 'stop', 'retry', 'retire', "
    "'publish_template', 'deprecate_template', 'revoke_template', "
    "'register_paper_probe')"
)
_REGISTRATION_AUDIT_DOWNGRADE_GUARD = r"""
DO $registration_audit_downgrade$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.runtime_audit_events
        WHERE action = 'register_paper_probe'
    ) THEN
        RAISE EXCEPTION 'registration_audit_downgrade_refused';
    END IF;
END
$registration_audit_downgrade$
"""


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
    _replace_action_check(_REGISTRATION_AUDIT_ACTION_CHECK)


def downgrade() -> None:
    _control_migration_schema()
    op.execute(_REGISTRATION_AUDIT_DOWNGRADE_GUARD)
    _replace_action_check(_TEMPLATE_AUDIT_ACTION_CHECK)
