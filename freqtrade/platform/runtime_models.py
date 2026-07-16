from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from freqtrade.platform.database import PlatformBase


class RuntimeInstanceRecord(PlatformBase):
    __tablename__ = "runtime_instances"
    __table_args__ = (
        CheckConstraint(
            "owner_kind IN ('migration_bot', 'paper_probe', 'workspace_worker')",
            name="ck_runtime_instances_owner_kind",
        ),
        CheckConstraint(
            "management_mode = 'supervisor'",
            name="ck_runtime_instances_management_mode",
        ),
        CheckConstraint(
            "environment IN ('paper', 'live')",
            name="ck_runtime_instances_environment",
        ),
        CheckConstraint(
            "desired_state IN ('stopped', 'running', 'retired')",
            name="ck_runtime_instances_desired_state",
        ),
        CheckConstraint(
            "lifecycle_status IN "
            "('registered', 'provisioning', 'stopped', 'starting', 'healthy', "
            "'stopping', 'failed', 'retired')",
            name="ck_runtime_instances_lifecycle_status",
        ),
        CheckConstraint(
            "optimistic_version >= 0",
            name="ck_runtime_instances_optimistic_version",
        ),
    )

    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    instance_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    management_mode: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime_spec_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    state_allocation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    desired_state: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_latched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    optimistic_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuntimeAttemptRecord(PlatformBase):
    __tablename__ = "runtime_attempts"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "attempt_number",
            name="uq_runtime_attempt_instance_number",
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="ck_runtime_attempts_attempt_number",
        ),
        CheckConstraint(
            "status IN "
            "('pending', 'validating', 'launching', 'healthy', 'stopping', 'stopped', 'failed')",
            name="ck_runtime_attempts_status",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "runtime_instances.instance_id",
            name="fk_runtime_attempts_instance_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_spec_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter_template_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_secret_versions: Mapped[dict] = mapped_column(JSON, nullable=False)
    image_id: Mapped[str] = mapped_column(String(256), nullable=False)
    root_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    backend_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    frontend_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    strategies_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    project_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    container_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    health_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


Index(
    "uq_runtime_attempt_active",
    RuntimeAttemptRecord.instance_id,
    unique=True,
    postgresql_where=RuntimeAttemptRecord.status.in_(
        ("pending", "validating", "launching", "healthy", "stopping")
    ),
)


class RuntimeLifecycleJobRecord(PlatformBase):
    __tablename__ = "runtime_lifecycle_jobs"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "idempotency_key",
            name="uq_runtime_job_instance_idempotency",
        ),
        CheckConstraint(
            "requested_action IN ('start', 'stop', 'retry', 'retire')",
            name="ck_runtime_lifecycle_jobs_requested_action",
        ),
        CheckConstraint(
            "expected_instance_version >= 0",
            name="ck_runtime_lifecycle_jobs_expected_instance_version",
        ),
        CheckConstraint(
            "lease_generation >= 0",
            name="ck_runtime_lifecycle_jobs_lease_generation",
        ),
        CheckConstraint(
            "status IN "
            "('pending', 'claimed', 'running', 'succeeded', 'failed', 'needs_reconciliation')",
            name="ck_runtime_lifecycle_jobs_status",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "runtime_instances.instance_id",
            name="fk_runtime_lifecycle_jobs_instance_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    requested_action: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_instance_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


Index(
    "uq_runtime_job_active",
    RuntimeLifecycleJobRecord.instance_id,
    unique=True,
    postgresql_where=RuntimeLifecycleJobRecord.status.in_(("pending", "claimed", "running")),
)


class RuntimeEndpointRecord(PlatformBase):
    __tablename__ = "runtime_endpoints"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "endpoint_kind",
            name="uq_runtime_endpoint_attempt_kind",
        ),
        CheckConstraint(
            "internal_port BETWEEN 1 AND 65535",
            name="ck_runtime_endpoints_internal_port",
        ),
        CheckConstraint(
            "protocol IN ('http', 'https')",
            name="ck_runtime_endpoints_protocol",
        ),
        CheckConstraint(
            "exposure_policy IN ('internal_only', 'none')",
            name="ck_runtime_endpoints_exposure_policy",
        ),
    )

    endpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "runtime_instances.instance_id",
            name="fk_runtime_endpoints_instance_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    attempt_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "runtime_attempts.attempt_id",
            name="fk_runtime_endpoints_attempt_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    endpoint_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    internal_port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)
    exposure_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeAccessRequestRecord(PlatformBase):
    __tablename__ = "runtime_access_requests"

    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "runtime_instances.instance_id",
            name="fk_runtime_access_requests_instance_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    attempt_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "runtime_attempts.attempt_id",
            name="fk_runtime_access_requests_attempt_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    route_policy_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuntimeAuditEventRecord(PlatformBase):
    __tablename__ = "runtime_audit_events"
    __table_args__ = (
        CheckConstraint(
            "owner_kind IN ('migration_bot', 'paper_probe', 'workspace_worker')",
            name="ck_runtime_audit_events_owner_kind",
        ),
        CheckConstraint(
            "action IN ('start', 'stop', 'retry', 'retire', "
            "'publish_template', 'deprecate_template', 'revoke_template', "
            "'register_paper_probe')",
            name="ck_runtime_audit_events_action",
        ),
    )

    audit_event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instance_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey(
            "runtime_instances.instance_id",
            name="fk_runtime_audit_events_instance_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    runtime_spec_revision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    adapter_template_revision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    next_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_code: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False)
