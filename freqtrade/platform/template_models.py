from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_models import (
    RuntimeAttemptRecord,
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
)


def _lower_hex_check(column_name: str) -> str:
    expression = column_name
    for character in "0123456789abcdef":
        expression = f"replace({expression}, '{character}', '')"
    return f"{expression} = ''"


class AdapterTemplateRevisionRecord(PlatformBase):
    __tablename__ = "adapter_template_revisions"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "semantic_version",
            name="uq_adapter_template_name_version",
        ),
        UniqueConstraint(
            "payload_digest",
            name="uq_adapter_template_payload_digest",
        ),
        CheckConstraint(
            "status IN ('active', 'deprecated', 'revoked')",
            name="ck_adapter_template_revisions_status",
        ),
        CheckConstraint(
            "length(payload_digest) = 64",
            name="ck_adapter_template_revisions_payload_digest_length",
        ),
    )

    adapter_template_revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(128), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    root_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    backend_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    frontend_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    strategies_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    published_by: Mapped[str] = mapped_column(String(128), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deprecated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class StateAllocationRecord(PlatformBase):
    __tablename__ = "state_allocations"
    __table_args__ = (
        UniqueConstraint(
            "relative_path",
            name="uq_state_allocation_relative_path",
        ),
        CheckConstraint(
            "provider_id = 'managed-local-v1'",
            name="ck_state_allocations_provider_id",
        ),
        CheckConstraint(
            "kind IN ('fresh', 'restored')",
            name="ck_state_allocations_kind",
        ),
        CheckConstraint(
            "status IN ('reserved', 'provisioning', 'ready', 'quarantined', 'retired')",
            name="ck_state_allocations_status",
        ),
        CheckConstraint(
            "generation >= 1",
            name="ck_state_allocations_generation",
        ),
        CheckConstraint(
            "instance_id <> '' AND "
            "replace(instance_id, '/', '') = instance_id AND "
            "replace(instance_id, '\\', '') = instance_id AND "
            "replace(instance_id, '.', '') = instance_id AND "
            "relative_path = 'ft_userdata/runtime/instances/' || instance_id",
            name="ck_state_allocations_relative_path",
        ),
    )

    state_allocation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    layout_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    restore_source_bundle_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index(
    "uq_state_allocation_active",
    StateAllocationRecord.instance_id,
    unique=True,
    postgresql_where=StateAllocationRecord.status.in_(("reserved", "provisioning", "ready")),
)


class SecretReferenceRecord(PlatformBase):
    __tablename__ = "secret_references"
    __table_args__ = (
        UniqueConstraint(
            "owner_kind",
            "owner_id",
            "owner_revision",
            "logical_name",
            name="uq_secret_reference_owner_logical_name",
        ),
        CheckConstraint(
            "provider_id = 'local-file-v1'",
            name="ck_secret_references_provider_id",
        ),
        CheckConstraint(
            "owner_kind IN ('migration_bot', 'paper_probe', 'workspace_worker')",
            name="ck_secret_references_owner_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'retired')",
            name="ck_secret_references_status",
        ),
    )

    secret_reference_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_class: Mapped[str] = mapped_column(String(128), nullable=False)
    logical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecretVersionMetadataRecord(PlatformBase):
    __tablename__ = "secret_version_metadata"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_secret_version_metadata_status",
        ),
    )

    secret_reference_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "secret_references.secret_reference_id",
            name="fk_secret_version_metadata_secret_reference_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index(
    "uq_secret_version_active",
    SecretVersionMetadataRecord.secret_reference_id,
    unique=True,
    postgresql_where=SecretVersionMetadataRecord.status == "active",
)


class RuntimeSpecRevisionRecord(PlatformBase):
    __tablename__ = "runtime_spec_revisions"
    __table_args__ = (
        UniqueConstraint(
            "payload_digest",
            name="uq_runtime_spec_payload_digest",
        ),
        CheckConstraint(
            "owner_kind IN ('migration_bot', 'paper_probe', 'workspace_worker')",
            name="ck_runtime_spec_revisions_owner_kind",
        ),
        CheckConstraint(
            "environment IN ('paper', 'live')",
            name="ck_runtime_spec_revisions_environment",
        ),
        CheckConstraint(
            "length(payload_digest) = 64",
            name="ck_runtime_spec_revisions_payload_digest_length",
        ),
        CheckConstraint(
            _lower_hex_check("payload_digest"),
            name="ck_runtime_spec_revisions_payload_digest_hex",
        ),
        CheckConstraint(
            "runtime_spec_revision_id = 'runtime-spec-' || payload_digest",
            name="ck_runtime_spec_revisions_revision_id",
        ),
    )

    runtime_spec_revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    instance_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    catalog_revision_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "platform_catalog_revisions.revision_id",
            name="fk_runtime_spec_revisions_catalog_revision_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    adapter_template_revision_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "adapter_template_revisions.adapter_template_revision_id",
            name="fk_runtime_spec_revisions_adapter_template_revision_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    state_allocation_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(
            "state_allocations.state_allocation_id",
            name="fk_runtime_spec_revisions_state_allocation_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    canonical_payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


for _table, _column, _target, _name in (
    (
        RuntimeInstanceRecord.__table__,
        "runtime_spec_revision_id",
        "runtime_spec_revisions.runtime_spec_revision_id",
        "fk_runtime_instances_runtime_spec_revision_id",
    ),
    (
        RuntimeInstanceRecord.__table__,
        "state_allocation_id",
        "state_allocations.state_allocation_id",
        "fk_runtime_instances_state_allocation_id",
    ),
    (
        RuntimeAttemptRecord.__table__,
        "runtime_spec_revision_id",
        "runtime_spec_revisions.runtime_spec_revision_id",
        "fk_runtime_attempts_runtime_spec_revision_id",
    ),
    (
        RuntimeAttemptRecord.__table__,
        "adapter_template_revision_id",
        "adapter_template_revisions.adapter_template_revision_id",
        "fk_runtime_attempts_adapter_template_revision_id",
    ),
    (
        RuntimeAuditEventRecord.__table__,
        "runtime_spec_revision_id",
        "runtime_spec_revisions.runtime_spec_revision_id",
        "fk_runtime_audit_events_runtime_spec_revision_id",
    ),
    (
        RuntimeAuditEventRecord.__table__,
        "adapter_template_revision_id",
        "adapter_template_revisions.adapter_template_revision_id",
        "fk_runtime_audit_events_adapter_template_revision_id",
    ),
):
    _table.append_constraint(
        ForeignKeyConstraint(
            [_column],
            [_target],
            name=_name,
            ondelete="RESTRICT",
        )
    )
