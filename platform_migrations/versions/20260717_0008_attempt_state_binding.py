"""Bind Runtime Attempts to the exact launch-time State Allocation generation.

Revision ID: 20260717_0008
Revises: 20260717_0007
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260717_0008"
down_revision: str | None = "20260717_0007"
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
_QUIESCENCE_GATE_SQL = r"""
DO $attempt_state_quiescence$
BEGIN
    LOCK TABLE public.runtime_instances,
               public.runtime_spec_revisions,
               public.state_allocations
    IN EXCLUSIVE MODE NOWAIT;
    LOCK TABLE public.runtime_attempts IN ACCESS EXCLUSIVE MODE NOWAIT;
EXCEPTION
    WHEN lock_not_available THEN
        RAISE EXCEPTION 'runtime_attempt_state_binding_quiescence_failed'
            USING ERRCODE = '55P03';
END
$attempt_state_quiescence$
"""
_LEGACY_GENERATION_GUARD_SQL = r"""
DO $attempt_state_generation$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.runtime_attempts AS attempt
        JOIN public.runtime_spec_revisions AS runtime_spec
          ON runtime_spec.runtime_spec_revision_id = attempt.runtime_spec_revision_id
        JOIN public.state_allocations AS allocation
          ON allocation.state_allocation_id = runtime_spec.state_allocation_id
        WHERE allocation.generation <> 1
    ) THEN
        RAISE EXCEPTION 'runtime_attempt_state_binding_generation_unprovable';
    END IF;
END
$attempt_state_generation$
"""
_LEGACY_BINDING_GUARD_SQL = r"""
DO $attempt_state_binding$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.runtime_attempts AS attempt
        LEFT JOIN public.runtime_instances AS instance
          ON instance.instance_id = attempt.instance_id
        LEFT JOIN public.runtime_spec_revisions AS runtime_spec
          ON runtime_spec.runtime_spec_revision_id = attempt.runtime_spec_revision_id
        LEFT JOIN public.state_allocations AS allocation
          ON allocation.state_allocation_id = runtime_spec.state_allocation_id
        WHERE instance.instance_id IS NULL
           OR runtime_spec.runtime_spec_revision_id IS NULL
           OR allocation.state_allocation_id IS NULL
           OR instance.runtime_spec_revision_id <> attempt.runtime_spec_revision_id
           OR instance.state_allocation_id <> runtime_spec.state_allocation_id
           OR allocation.generation < 1
    ) THEN
        RAISE EXCEPTION 'runtime_attempt_state_binding_backfill_failed';
    END IF;
END
$attempt_state_binding$
"""
_BACKFILL_BINDING_SQL = r"""
UPDATE public.runtime_attempts AS attempt
SET state_allocation_id = runtime_spec.state_allocation_id,
    state_allocation_generation = allocation.generation
FROM public.runtime_instances AS instance,
     public.runtime_spec_revisions AS runtime_spec,
     public.state_allocations AS allocation
WHERE instance.instance_id = attempt.instance_id
  AND runtime_spec.runtime_spec_revision_id = attempt.runtime_spec_revision_id
  AND instance.runtime_spec_revision_id = attempt.runtime_spec_revision_id
  AND instance.state_allocation_id = runtime_spec.state_allocation_id
  AND allocation.state_allocation_id = runtime_spec.state_allocation_id
"""
_DOWNGRADE_GUARD_SQL = r"""
DO $attempt_state_binding_downgrade$
BEGIN
    IF EXISTS (SELECT 1 FROM public.runtime_attempts) THEN
        RAISE EXCEPTION 'runtime_attempt_state_binding_downgrade_refused';
    END IF;
END
$attempt_state_binding_downgrade$
"""


def _control_migration_schema() -> None:
    op.execute(_CONTROLLED_SCHEMA_SQL)
    op.execute(_CONTROLLED_SCHEMA_GUARD_SQL)


def _require_quiescence() -> None:
    op.execute(_QUIESCENCE_GATE_SQL)


def upgrade() -> None:
    _control_migration_schema()
    _require_quiescence()
    op.add_column(
        "runtime_attempts",
        sa.Column("state_allocation_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "runtime_attempts",
        sa.Column("state_allocation_generation", sa.Integer(), nullable=True),
    )
    op.execute(_LEGACY_GENERATION_GUARD_SQL)
    op.execute(_LEGACY_BINDING_GUARD_SQL)
    op.execute(_BACKFILL_BINDING_SQL)
    op.alter_column("runtime_attempts", "state_allocation_id", nullable=False)
    op.alter_column("runtime_attempts", "state_allocation_generation", nullable=False)
    op.create_foreign_key(
        "fk_runtime_attempts_state_allocation_id",
        "runtime_attempts",
        "state_allocations",
        ["state_allocation_id"],
        ["state_allocation_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_runtime_attempts_state_allocation_generation",
        "runtime_attempts",
        "state_allocation_generation >= 1",
    )


def downgrade() -> None:
    _control_migration_schema()
    _require_quiescence()
    op.execute(_DOWNGRADE_GUARD_SQL)
    op.drop_constraint(
        "ck_runtime_attempts_state_allocation_generation",
        "runtime_attempts",
        type_="check",
    )
    op.drop_constraint(
        "fk_runtime_attempts_state_allocation_id",
        "runtime_attempts",
        type_="foreignkey",
    )
    op.drop_column("runtime_attempts", "state_allocation_generation")
    op.drop_column("runtime_attempts", "state_allocation_id")
