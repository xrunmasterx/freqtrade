"""Fence runtime supervisor leases with a monotonic generation.

Revision ID: 20260717_0005
Revises: 20260714_0004
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260717_0005"
down_revision: str | None = "20260714_0004"
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
_RUNTIME_TABLE_LOCK_SQL = (
    "LOCK TABLE public.runtime_lifecycle_jobs IN ACCESS EXCLUSIVE MODE",
    "LOCK TABLE public.runtime_attempts IN ACCESS EXCLUSIVE MODE",
)
_DOWNGRADE_GUARD_SQL = r"""
DO $runtime_lease_generation_downgrade$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.runtime_lifecycle_jobs
        WHERE lease_generation <> 0
    ) OR EXISTS (
        SELECT 1
        FROM public.runtime_attempts
        WHERE health_result ->> 'profile_id' = 'legacy-runtime-health-v1'
    ) THEN
        RAISE EXCEPTION 'runtime_task6_downgrade_refused';
    END IF;
END
$runtime_lease_generation_downgrade$
"""
_NORMALIZE_LEGACY_HEALTH_SQL = r"""
UPDATE public.runtime_attempts
SET health_result = json_build_object(
    'profile_id', 'legacy-runtime-health-v1',
    'profile_digest', '7170fbfdbd0ef803f72881ded2e7099683eb4a4c71c5e5bb7c1a5f5f1c0f7cff',
    'deadline_at', COALESCE(
        started_at,
        stopped_at,
        TIMESTAMPTZ '1970-01-01 00:00:00+00'
    ),
    'next_probe_not_before', COALESCE(
        started_at,
        stopped_at,
        TIMESTAMPTZ '1970-01-01 00:00:00+00'
    ),
    'observed_at', COALESCE(
        stopped_at,
        started_at,
        TIMESTAMPTZ '1970-01-01 00:00:00+00'
    ),
    'attempts', 1,
    'result_code', CASE
        WHEN status = 'healthy' AND health_result ->> 'result_code' = 'healthy'
            THEN 'health_probe_healthy'
        ELSE 'health_probe_unknown'
    END,
    'last_failure_code', CASE
        WHEN status = 'healthy' AND health_result ->> 'result_code' = 'healthy'
            THEN NULL
        WHEN failure_code ~ '^[a-z0-9][a-z0-9_-]{0,127}$'
            THEN failure_code
        WHEN health_result ->> 'result_code' ~ '^[a-z0-9][a-z0-9_-]{0,127}$'
            THEN health_result ->> 'result_code'
        ELSE 'legacy_health_unknown'
    END
)
WHERE health_result IS NOT NULL
"""
_QUIESCENCE_GUARD_SQL = r"""
DO $runtime_lease_generation_quiescence$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.runtime_lifecycle_jobs
        WHERE status IN ('claimed', 'running')
    ) THEN
        RAISE EXCEPTION 'runtime_lease_generation_requires_quiescence';
    END IF;
END
$runtime_lease_generation_quiescence$
"""


def _control_migration_schema() -> None:
    op.execute(_CONTROLLED_SCHEMA_SQL)
    op.execute(_CONTROLLED_SCHEMA_GUARD_SQL)


def _lock_runtime_tables() -> None:
    for statement in _RUNTIME_TABLE_LOCK_SQL:
        op.execute(statement)


def upgrade() -> None:
    _control_migration_schema()
    _lock_runtime_tables()
    op.execute(_QUIESCENCE_GUARD_SQL)
    op.add_column(
        "runtime_lifecycle_jobs",
        sa.Column(
            "lease_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_runtime_lifecycle_jobs_lease_generation",
        "runtime_lifecycle_jobs",
        "lease_generation >= 0",
    )
    op.alter_column(
        "runtime_lifecycle_jobs",
        "lease_generation",
        server_default=None,
    )
    op.execute(_NORMALIZE_LEGACY_HEALTH_SQL)


def downgrade() -> None:
    _control_migration_schema()
    _lock_runtime_tables()
    op.execute(_DOWNGRADE_GUARD_SQL)
    op.drop_constraint(
        "ck_runtime_lifecycle_jobs_lease_generation",
        "runtime_lifecycle_jobs",
        type_="check",
    )
    op.drop_column("runtime_lifecycle_jobs", "lease_generation")
