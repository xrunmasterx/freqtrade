from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Protocol, TypeVar, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy import Engine, func, select, tuple_
from sqlalchemy.orm import Session

from freqtrade.platform.runtime_domain import (
    Identifier,
    RuntimeAction,
    RuntimeAttemptStatus,
    RuntimeAttemptView,
    RuntimeAuditAction,
    RuntimeDesiredState,
    RuntimeInstanceView,
    RuntimeJobStatus,
    RuntimeJobView,
    RuntimeLifecycleCommand,
    RuntimeLifecycleStatus,
    RuntimeManagementMode,
    RuntimeOwnerKind,
    RuntimeOwnerRef,
)
from freqtrade.platform.runtime_models import (
    RuntimeAttemptRecord,
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
    RuntimeLifecycleJobRecord,
)
from freqtrade.platform.runtime_spec import RuntimeSpecPayload
from freqtrade.platform.template_models import (
    AdapterTemplateRevisionRecord,
    RuntimeSpecRevisionRecord,
    SecretReferenceRecord,
    SecretVersionMetadataRecord,
)


Clock = Callable[[], datetime]
IdFactory = Callable[[str], str]
CompletionStatus = Literal["succeeded", "failed"]
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_ACTIVE_ATTEMPT_STATUSES = (
    "pending",
    "validating",
    "launching",
    "healthy",
    "stopping",
)
_ACTIVE_JOB_STATUSES = ("pending", "claimed", "running")
_LEASED_JOB_STATUSES = ("claimed", "running")
_AUDIT_SOURCE = "runtime_repository"
_ViewT = TypeVar("_ViewT")
_CommitIdentity = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
_ImageIdentity = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class RuntimeNotFound(RuntimeError):
    pass


class RuntimeConflict(RuntimeError):
    pass


class RuntimeDataError(RuntimeError):
    pass


class RuntimeInvalidTransition(RuntimeError):
    pass


class _RuntimeRepositoryInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RuntimeInstanceAuditState(_RuntimeRepositoryInput):
    desired_state: RuntimeDesiredState
    lifecycle_status: RuntimeLifecycleStatus
    failure_latched: bool
    optimistic_version: int = Field(ge=0)


class RuntimeAuditEvent(_RuntimeRepositoryInput):
    actor_type: Identifier
    request_id: Identifier
    idempotency_key: Identifier | None
    owner_kind: RuntimeOwnerKind | None
    owner_id: Identifier | None
    owner_revision: Identifier | None
    instance_id: Identifier | None
    runtime_spec_revision_id: Identifier | None
    adapter_template_revision_id: Identifier | None
    action: RuntimeAuditAction
    previous_state: RuntimeInstanceAuditState | None
    next_state: RuntimeInstanceAuditState | None
    result_code: Identifier


class ResolvedSecretVersion(_RuntimeRepositoryInput):
    secret_reference_id: Identifier
    version_id: Identifier


class ResolvedRuntimeMaterial(_RuntimeRepositoryInput):
    runtime_spec_revision_id: Identifier
    adapter_template_revision_id: Identifier
    state_allocation_id: Identifier
    resolved_secret_versions: tuple[ResolvedSecretVersion, ...]
    image_id: _ImageIdentity
    root_commit: _CommitIdentity
    backend_commit: _CommitIdentity
    frontend_commit: _CommitIdentity
    strategies_commit: _CommitIdentity
    project_identity: Identifier
    container_identity: Identifier

    @field_validator("resolved_secret_versions", mode="before")
    @classmethod
    def normalize_secret_versions(cls, value: object) -> object:
        if isinstance(value, dict):
            return tuple(
                {"secret_reference_id": reference_id, "version_id": version_id}
                for reference_id, version_id in sorted(value.items())
            )
        return value

    @field_validator("resolved_secret_versions")
    @classmethod
    def require_unique_secret_references(
        cls,
        value: tuple[ResolvedSecretVersion, ...],
    ) -> tuple[ResolvedSecretVersion, ...]:
        if len({item.secret_reference_id for item in value}) != len(value):
            raise ValueError("duplicate secret reference")
        return value


@runtime_checkable
class RuntimeQueryRepository(Protocol):
    def get_instance(self, instance_id: Identifier) -> RuntimeInstanceView: ...

    def list_instances(self) -> tuple[RuntimeInstanceView, ...]: ...

    def list_attempts(self, instance_id: Identifier) -> tuple[RuntimeAttemptView, ...]: ...

    def list_jobs(self, instance_id: Identifier) -> tuple[RuntimeJobView, ...]: ...


@runtime_checkable
class RuntimeRepository(RuntimeQueryRepository, Protocol):
    def create_job(
        self,
        command: RuntimeLifecycleCommand,
        actor: Identifier,
    ) -> RuntimeJobView: ...

    def claim_next_job(
        self,
        lease_owner: Identifier,
        lease_seconds: int,
    ) -> RuntimeJobView | None: ...

    def complete_job(
        self,
        job_id: Identifier,
        status: CompletionStatus,
        failure_code: Identifier | None,
    ) -> RuntimeJobView: ...

    def begin_attempt(
        self,
        job_id: Identifier,
        resolved_material: ResolvedRuntimeMaterial,
    ) -> RuntimeAttemptView: ...

    def record_healthy(
        self,
        job_id: Identifier,
        attempt_id: Identifier,
    ) -> RuntimeAttemptView: ...

    def record_failed(
        self,
        job_id: Identifier,
        attempt_id: Identifier,
        failure_code: Identifier,
    ) -> RuntimeAttemptView: ...

    def record_stopped(
        self,
        job_id: Identifier,
        attempt_id: Identifier,
        exit_code: int,
    ) -> RuntimeAttemptView: ...

    def renew_lease(
        self,
        job_id: Identifier,
        lease_owner: Identifier,
        lease_seconds: int,
    ) -> RuntimeJobView: ...

    def latch_failure(
        self,
        job_id: Identifier,
        failure_code: Identifier,
    ) -> RuntimeJobView: ...

    def append_audit(self, event: RuntimeAuditEvent) -> None: ...


def _system_clock() -> datetime:
    return datetime.now(UTC)


def _uuid_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _health_result_summary(evidence: object) -> str | None:
    if evidence is None:
        return None
    if not isinstance(evidence, dict):
        raise RuntimeDataError("invalid_health_result")
    result_code = evidence.get("result_code")
    if not isinstance(result_code, str):
        raise RuntimeDataError("invalid_health_result")
    try:
        return _IDENTIFIER_ADAPTER.validate_python(result_code)
    except ValidationError:
        raise RuntimeDataError("invalid_health_result") from None


def _registry_view(factory: Callable[[], _ViewT]) -> _ViewT:
    try:
        return factory()
    except (ValueError, ValidationError):
        raise RuntimeDataError("invalid_registry_data") from None


class SqlRuntimeRepository:
    def __init__(
        self,
        engine: Engine,
        clock: Clock = _system_clock,
        id_factory: IdFactory = _uuid_id,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._id_factory = id_factory

    def get_instance(self, instance_id: Identifier) -> RuntimeInstanceView:
        with Session(self._engine) as session:
            record = session.get(RuntimeInstanceRecord, instance_id)
            if record is None:
                raise RuntimeNotFound("runtime_instance_not_found")
            return self._instance_view(record)

    def list_instances(self) -> tuple[RuntimeInstanceView, ...]:
        with Session(self._engine) as session:
            records = session.scalars(
                select(RuntimeInstanceRecord).order_by(
                    RuntimeInstanceRecord.created_at,
                    RuntimeInstanceRecord.instance_id,
                )
            )
            return tuple(self._instance_view(record) for record in records)

    def list_attempts(self, instance_id: Identifier) -> tuple[RuntimeAttemptView, ...]:
        with Session(self._engine) as session:
            self._require_instance(session, instance_id)
            records = session.scalars(
                select(RuntimeAttemptRecord)
                .where(RuntimeAttemptRecord.instance_id == instance_id)
                .order_by(
                    RuntimeAttemptRecord.attempt_number,
                    RuntimeAttemptRecord.attempt_id,
                )
            )
            return tuple(self._attempt_view(record) for record in records)

    def list_jobs(self, instance_id: Identifier) -> tuple[RuntimeJobView, ...]:
        with Session(self._engine) as session:
            self._require_instance(session, instance_id)
            records = session.scalars(
                select(RuntimeLifecycleJobRecord)
                .where(RuntimeLifecycleJobRecord.instance_id == instance_id)
                .order_by(
                    RuntimeLifecycleJobRecord.requested_at,
                    RuntimeLifecycleJobRecord.job_id,
                )
            )
            return tuple(self._job_view(record) for record in records)

    def create_job(
        self,
        command: RuntimeLifecycleCommand,
        actor: Identifier,
    ) -> RuntimeJobView:
        validated_actor = _IDENTIFIER_ADAPTER.validate_python(actor)
        now = self._now()
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            instance = session.scalar(
                select(RuntimeInstanceRecord)
                .where(RuntimeInstanceRecord.instance_id == command.instance_id)
                .with_for_update()
            )
            if instance is None:
                raise RuntimeNotFound("runtime_instance_not_found")

            existing = session.scalar(
                select(RuntimeLifecycleJobRecord).where(
                    RuntimeLifecycleJobRecord.instance_id == command.instance_id,
                    RuntimeLifecycleJobRecord.idempotency_key == command.idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.requested_action == command.action
                    and existing.expected_instance_version == command.expected_instance_version
                ):
                    return self._job_view(existing)
                raise RuntimeConflict("idempotency_key_conflict")

            blocking_statuses = tuple(
                session.scalars(
                    select(RuntimeLifecycleJobRecord.status).where(
                        RuntimeLifecycleJobRecord.instance_id == command.instance_id,
                        RuntimeLifecycleJobRecord.status.in_(
                            (*_ACTIVE_JOB_STATUSES, "needs_reconciliation")
                        ),
                    )
                )
            )
            if "needs_reconciliation" in blocking_statuses:
                raise RuntimeConflict("reconciliation_required")
            if any(status in _ACTIVE_JOB_STATUSES for status in blocking_statuses):
                raise RuntimeConflict("active_job_exists")
            if instance.optimistic_version != command.expected_instance_version:
                raise RuntimeConflict("stale_instance_version")

            previous_state = self._audit_state(instance)
            status = self._apply_command(session, instance, command.action, now)
            instance.optimistic_version += 1
            next_state = self._audit_state(instance)
            job = RuntimeLifecycleJobRecord(
                job_id=self._new_id("job"),
                instance_id=instance.instance_id,
                requested_action=command.action,
                idempotency_key=command.idempotency_key,
                expected_instance_version=command.expected_instance_version,
                status=status,
                lease_owner=None,
                lease_expires_at=None,
                requested_at=now,
                started_at=None,
                completed_at=now if status == "succeeded" else None,
                failure_code=None,
            )
            session.add(job)
            self._append_audit_record(
                session,
                RuntimeAuditEvent(
                    actor_type=validated_actor,
                    request_id=job.job_id,
                    idempotency_key=job.idempotency_key,
                    owner_kind=instance.owner_kind,
                    owner_id=instance.owner_id,
                    owner_revision=instance.owner_revision,
                    instance_id=instance.instance_id,
                    runtime_spec_revision_id=instance.runtime_spec_revision_id,
                    adapter_template_revision_id=None,
                    action=RuntimeAuditAction(command.action.value),
                    previous_state=previous_state,
                    next_state=next_state,
                    result_code="accepted",
                ),
                now,
            )
            return self._job_view(job)

    def claim_next_job(
        self,
        lease_owner: Identifier,
        lease_seconds: int,
    ) -> RuntimeJobView | None:
        validated_owner = _IDENTIFIER_ADAPTER.validate_python(lease_owner)
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 1 <= lease_seconds <= 3600
        ):
            raise RuntimeInvalidTransition("invalid_lease_seconds")
        now = self._now()
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            expired = session.scalar(
                select(RuntimeLifecycleJobRecord)
                .where(
                    RuntimeLifecycleJobRecord.status.in_(_LEASED_JOB_STATUSES),
                    RuntimeLifecycleJobRecord.lease_expires_at <= now,
                )
                .order_by(
                    RuntimeLifecycleJobRecord.lease_expires_at,
                    RuntimeLifecycleJobRecord.job_id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if expired is not None:
                self._mark_reconciliation(session, expired, validated_owner, now)
                return self._job_view(expired)

            statement = (
                select(RuntimeLifecycleJobRecord)
                .where(RuntimeLifecycleJobRecord.status == "pending")
                .order_by(
                    RuntimeLifecycleJobRecord.requested_at,
                    RuntimeLifecycleJobRecord.job_id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = session.scalar(statement)
            if job is None:
                return None
            job.status = "claimed"
            job.started_at = job.started_at or now
            job.lease_owner = validated_owner
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            self._append_job_audit(session, job, validated_owner, "claimed", now)
            return self._job_view(job)

    def complete_job(
        self,
        job_id: Identifier,
        status: CompletionStatus,
        failure_code: Identifier | None,
    ) -> RuntimeJobView:
        _IDENTIFIER_ADAPTER.validate_python(job_id)
        if status not in {"succeeded", "failed"}:
            raise RuntimeInvalidTransition("invalid_completion_status")
        if status == "succeeded" and failure_code is not None:
            raise RuntimeInvalidTransition("success_failure_code_forbidden")
        if status == "failed" and failure_code is None:
            raise RuntimeInvalidTransition("failure_code_required")
        validated_failure_code = (
            _IDENTIFIER_ADAPTER.validate_python(failure_code) if failure_code is not None else None
        )
        now = self._now()
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            job = session.scalar(
                select(RuntimeLifecycleJobRecord)
                .where(RuntimeLifecycleJobRecord.job_id == job_id)
                .with_for_update()
            )
            if job is None:
                raise RuntimeNotFound("runtime_job_not_found")
            if job.status == "running":
                raise RuntimeInvalidTransition("attempt_transition_required")
            if job.status not in _LEASED_JOB_STATUSES:
                raise RuntimeInvalidTransition("job_not_completable")

            actor = job.lease_owner or "runtime_supervisor"
            lease_expires_at = _aware_utc(job.lease_expires_at)
            if lease_expires_at is None or lease_expires_at <= now:
                self._mark_reconciliation(session, job, actor, now)
                return self._job_view(job)

            job.status = status
            job.completed_at = now
            job.failure_code = validated_failure_code
            job.lease_owner = None
            job.lease_expires_at = None
            self._append_job_audit(session, job, actor, status, now)
            return self._job_view(job)

    def begin_attempt(
        self,
        job_id: Identifier,
        resolved_material: ResolvedRuntimeMaterial,
    ) -> RuntimeAttemptView:
        if not isinstance(resolved_material, ResolvedRuntimeMaterial):
            raise RuntimeInvalidTransition("resolved_material_type_required")
        validated_material = self._revalidate_resolved_material(resolved_material)
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            job, instance = self._lock_job_and_instance(session, job_id)
            active_attempt = self._lock_active_attempt(session, instance.instance_id)
            self._validate_resolved_material(session, instance, validated_material)
            now = self._now()
            actor = self._require_current_lease(job, now)
            if job.requested_action not in {"start", "retry"}:
                raise RuntimeInvalidTransition("attempt_requires_start_or_retry_job")
            if active_attempt is not None:
                raise RuntimeInvalidTransition("active_attempt_exists")

            attempt_number = (
                session.scalar(
                    select(func.max(RuntimeAttemptRecord.attempt_number)).where(
                        RuntimeAttemptRecord.instance_id == instance.instance_id
                    )
                )
                or 0
            ) + 1
            previous_state = self._audit_state(instance)
            instance.lifecycle_status = "starting"
            job.status = "running"
            attempt = RuntimeAttemptRecord(
                attempt_id=self._new_id("attempt"),
                instance_id=instance.instance_id,
                attempt_number=attempt_number,
                runtime_spec_revision_id=validated_material.runtime_spec_revision_id,
                adapter_template_revision_id=validated_material.adapter_template_revision_id,
                resolved_secret_versions=self._secret_version_mapping(validated_material),
                image_id=validated_material.image_id,
                root_commit=validated_material.root_commit,
                backend_commit=validated_material.backend_commit,
                frontend_commit=validated_material.frontend_commit,
                strategies_commit=validated_material.strategies_commit,
                project_identity=validated_material.project_identity,
                container_identity=validated_material.container_identity,
                status="launching",
                health_result=None,
                started_at=now,
                stopped_at=None,
                exit_code=None,
                failure_code=None,
            )
            session.add(attempt)
            self._append_supervisor_audit(
                session,
                job,
                instance,
                actor,
                validated_material.adapter_template_revision_id,
                previous_state,
                self._audit_state(instance),
                "attempt_started",
                now,
            )
            return self._attempt_view(attempt)

    def record_healthy(
        self,
        job_id: Identifier,
        attempt_id: Identifier,
    ) -> RuntimeAttemptView:
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            job, instance, attempt = self._lock_transition_records(session, job_id, attempt_id)
            now = self._now()
            actor = self._require_current_lease(job, now)
            if job.status != "running" or job.requested_action not in {"start", "retry"}:
                raise RuntimeInvalidTransition("healthy_requires_running_start_or_retry_job")
            if attempt.status not in {"pending", "validating", "launching"}:
                raise RuntimeInvalidTransition("attempt_not_health_transitionable")
            if instance.desired_state != "running":
                raise RuntimeInvalidTransition("healthy_requires_running_desired_state")

            previous_state = self._audit_state(instance)
            attempt.status = "healthy"
            attempt.health_result = {"result_code": "healthy"}
            instance.lifecycle_status = "healthy"
            instance.failure_latched = False
            self._complete_leased_job(job, "succeeded", None, now)
            self._append_supervisor_audit(
                session,
                job,
                instance,
                actor,
                attempt.adapter_template_revision_id,
                previous_state,
                self._audit_state(instance),
                "healthy",
                now,
            )
            return self._attempt_view(attempt)

    def record_failed(
        self,
        job_id: Identifier,
        attempt_id: Identifier,
        failure_code: Identifier,
    ) -> RuntimeAttemptView:
        validated_failure_code = _IDENTIFIER_ADAPTER.validate_python(failure_code)
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            job, instance, attempt = self._lock_transition_records(session, job_id, attempt_id)
            now = self._now()
            actor = self._require_current_lease(job, now)
            if job.status != "running" or job.requested_action not in {"start", "retry"}:
                raise RuntimeInvalidTransition("failed_requires_running_start_or_retry_job")
            if attempt.status not in _ACTIVE_ATTEMPT_STATUSES:
                raise RuntimeInvalidTransition("attempt_not_failure_transitionable")

            previous_state = self._audit_state(instance)
            attempt.status = "failed"
            attempt.stopped_at = now
            attempt.failure_code = validated_failure_code
            instance.lifecycle_status = "failed"
            instance.failure_latched = True
            self._complete_leased_job(job, "failed", validated_failure_code, now)
            self._append_supervisor_audit(
                session,
                job,
                instance,
                actor,
                attempt.adapter_template_revision_id,
                previous_state,
                self._audit_state(instance),
                validated_failure_code,
                now,
            )
            return self._attempt_view(attempt)

    def record_stopped(
        self,
        job_id: Identifier,
        attempt_id: Identifier,
        exit_code: int,
    ) -> RuntimeAttemptView:
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise RuntimeInvalidTransition("invalid_exit_code")
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            job, instance, attempt = self._lock_transition_records(session, job_id, attempt_id)
            now = self._now()
            actor = self._require_current_lease(job, now)
            if job.requested_action != "stop":
                raise RuntimeInvalidTransition("stopped_requires_stop_job")
            if attempt.status not in _ACTIVE_ATTEMPT_STATUSES:
                raise RuntimeInvalidTransition("attempt_not_stop_transitionable")
            if instance.desired_state != "stopped":
                raise RuntimeInvalidTransition("stopped_requires_stopped_desired_state")

            previous_state = self._audit_state(instance)
            attempt.status = "stopped"
            attempt.stopped_at = now
            attempt.exit_code = exit_code
            instance.lifecycle_status = "stopped"
            self._complete_leased_job(job, "succeeded", None, now)
            self._append_supervisor_audit(
                session,
                job,
                instance,
                actor,
                attempt.adapter_template_revision_id,
                previous_state,
                self._audit_state(instance),
                "stopped",
                now,
            )
            return self._attempt_view(attempt)

    def renew_lease(
        self,
        job_id: Identifier,
        lease_owner: Identifier,
        lease_seconds: int,
    ) -> RuntimeJobView:
        validated_owner = _IDENTIFIER_ADAPTER.validate_python(lease_owner)
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 1 <= lease_seconds <= 3600
        ):
            raise RuntimeInvalidTransition("invalid_lease_seconds")
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            job, instance = self._lock_job_and_instance(session, job_id)
            active_attempt = self._lock_active_attempt(session, instance.instance_id)
            adapter_template_revision_id = (
                active_attempt.adapter_template_revision_id
                if active_attempt is not None
                else self._runtime_spec_template_binding(session, instance)
            )
            now = self._now()
            self._require_current_lease(job, now)
            if job.lease_owner != validated_owner:
                raise RuntimeInvalidTransition("lease_owner_mismatch")

            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            state = self._audit_state(instance)
            self._append_supervisor_audit(
                session,
                job,
                instance,
                validated_owner,
                adapter_template_revision_id,
                state,
                state,
                "lease_renewed",
                now,
            )
            return self._job_view(job)

    def latch_failure(
        self,
        job_id: Identifier,
        failure_code: Identifier,
    ) -> RuntimeJobView:
        validated_failure_code = _IDENTIFIER_ADAPTER.validate_python(failure_code)
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            job, instance = self._lock_job_and_instance(session, job_id)
            active_attempt = self._lock_active_attempt(session, instance.instance_id)
            adapter_template_revision_id = (
                self._runtime_spec_template_binding(session, instance)
                if active_attempt is None
                else None
            )
            now = self._now()
            actor = self._require_current_lease(job, now)
            if active_attempt is not None:
                raise RuntimeInvalidTransition("active_attempt_requires_explicit_failure")

            previous_state = self._audit_state(instance)
            instance.lifecycle_status = "failed"
            instance.failure_latched = True
            self._complete_leased_job(job, "failed", validated_failure_code, now)
            self._append_supervisor_audit(
                session,
                job,
                instance,
                actor,
                adapter_template_revision_id,
                previous_state,
                self._audit_state(instance),
                validated_failure_code,
                now,
            )
            return self._job_view(job)

    def append_audit(self, event: RuntimeAuditEvent) -> None:
        with Session(self._engine) as session, session.begin():
            self._append_audit_record(session, event, self._now())

    @staticmethod
    def _revalidate_resolved_material(
        material: ResolvedRuntimeMaterial,
    ) -> ResolvedRuntimeMaterial:
        try:
            primitive_material = material.model_dump(mode="json")
            return ResolvedRuntimeMaterial.model_validate(primitive_material)
        except (TypeError, ValueError):
            raise RuntimeInvalidTransition("invalid_resolved_material") from None

    @staticmethod
    def _secret_version_mapping(material: ResolvedRuntimeMaterial) -> dict[str, str]:
        return {
            item.secret_reference_id: item.version_id
            for item in material.resolved_secret_versions
        }

    @staticmethod
    def _lock_job_and_instance(
        session: Session,
        job_id: Identifier,
    ) -> tuple[RuntimeLifecycleJobRecord, RuntimeInstanceRecord]:
        validated_job_id = _IDENTIFIER_ADAPTER.validate_python(job_id)
        job = session.scalar(
            select(RuntimeLifecycleJobRecord)
            .where(RuntimeLifecycleJobRecord.job_id == validated_job_id)
            .with_for_update()
        )
        if job is None:
            raise RuntimeNotFound("runtime_job_not_found")
        instance = session.scalar(
            select(RuntimeInstanceRecord)
            .where(RuntimeInstanceRecord.instance_id == job.instance_id)
            .with_for_update()
        )
        if instance is None:
            raise RuntimeNotFound("runtime_instance_not_found")
        return job, instance

    @staticmethod
    def _lock_active_attempt(
        session: Session,
        instance_id: str,
    ) -> RuntimeAttemptRecord | None:
        return session.scalar(
            select(RuntimeAttemptRecord)
            .where(
                RuntimeAttemptRecord.instance_id == instance_id,
                RuntimeAttemptRecord.status.in_(_ACTIVE_ATTEMPT_STATUSES),
            )
            .order_by(RuntimeAttemptRecord.attempt_number, RuntimeAttemptRecord.attempt_id)
            .with_for_update()
            .limit(1)
        )

    def _lock_transition_records(
        self,
        session: Session,
        job_id: Identifier,
        attempt_id: Identifier,
    ) -> tuple[RuntimeLifecycleJobRecord, RuntimeInstanceRecord, RuntimeAttemptRecord]:
        job, instance = self._lock_job_and_instance(session, job_id)
        validated_attempt_id = _IDENTIFIER_ADAPTER.validate_python(attempt_id)
        attempt = session.scalar(
            select(RuntimeAttemptRecord)
            .where(RuntimeAttemptRecord.attempt_id == validated_attempt_id)
            .with_for_update()
        )
        if attempt is None:
            raise RuntimeNotFound("runtime_attempt_not_found")
        if attempt.instance_id != instance.instance_id:
            raise RuntimeInvalidTransition("job_attempt_instance_mismatch")
        return job, instance, attempt

    @staticmethod
    def _require_current_lease(job: RuntimeLifecycleJobRecord, now: datetime) -> str:
        if job.status not in _LEASED_JOB_STATUSES:
            raise RuntimeInvalidTransition("job_not_leased")
        if job.lease_owner is None:
            raise RuntimeInvalidTransition("job_lease_owner_required")
        lease_expires_at = _aware_utc(job.lease_expires_at)
        if lease_expires_at is None or lease_expires_at <= now:
            raise RuntimeInvalidTransition("lease_expired")
        return job.lease_owner

    @staticmethod
    def _validate_resolved_material(
        session: Session,
        instance: RuntimeInstanceRecord,
        material: ResolvedRuntimeMaterial,
    ) -> None:
        if material.runtime_spec_revision_id != instance.runtime_spec_revision_id:
            raise RuntimeInvalidTransition("runtime_spec_mismatch")
        if material.state_allocation_id != instance.state_allocation_id:
            raise RuntimeInvalidTransition("state_allocation_mismatch")
        runtime_spec = session.get(RuntimeSpecRevisionRecord, instance.runtime_spec_revision_id)
        if runtime_spec is None:
            raise RuntimeDataError("runtime_spec_not_found")
        if material.adapter_template_revision_id != runtime_spec.adapter_template_revision_id:
            raise RuntimeInvalidTransition("template_mismatch")
        if material.state_allocation_id != runtime_spec.state_allocation_id:
            raise RuntimeInvalidTransition("state_allocation_mismatch")
        runtime_spec_payload = SqlRuntimeRepository._runtime_spec_payload(
            runtime_spec.canonical_payload
        )
        resolved_secret_versions = SqlRuntimeRepository._secret_version_mapping(material)
        if set(resolved_secret_versions) != set(runtime_spec_payload.secret_reference_ids):
            raise RuntimeInvalidTransition("secret_reference_set_mismatch")
        template, references, versions = SqlRuntimeRepository._lock_provenance_rows(
            session,
            material.adapter_template_revision_id,
            resolved_secret_versions,
        )
        SqlRuntimeRepository._validate_locked_template(material, template)
        SqlRuntimeRepository._validate_locked_secret_versions(
            instance,
            resolved_secret_versions,
            references,
            versions,
        )

    @staticmethod
    def _runtime_spec_payload(canonical_payload: str) -> RuntimeSpecPayload:
        try:
            return RuntimeSpecPayload.model_validate_json(canonical_payload)
        except ValidationError:
            raise RuntimeDataError("invalid_runtime_spec_payload") from None

    @staticmethod
    def _lock_provenance_rows(
        session: Session,
        adapter_template_revision_id: str,
        resolved_secret_versions: dict[str, str],
    ) -> tuple[
        AdapterTemplateRevisionRecord,
        tuple[SecretReferenceRecord, ...],
        tuple[SecretVersionMetadataRecord, ...],
    ]:
        template = session.scalar(
            select(AdapterTemplateRevisionRecord)
            .where(
                AdapterTemplateRevisionRecord.adapter_template_revision_id
                == adapter_template_revision_id
            )
            .with_for_update()
        )
        if template is None:
            raise RuntimeDataError("adapter_template_not_found")

        secret_reference_ids = tuple(sorted(resolved_secret_versions))
        references = tuple(
            session.scalars(
                select(SecretReferenceRecord)
                .where(SecretReferenceRecord.secret_reference_id.in_(secret_reference_ids))
                .order_by(SecretReferenceRecord.secret_reference_id)
                .with_for_update()
            )
        )
        if len(references) != len(secret_reference_ids):
            raise RuntimeInvalidTransition("secret_reference_not_found")

        secret_version_ids = tuple(sorted(resolved_secret_versions.items()))
        versions = tuple(
            session.scalars(
                select(SecretVersionMetadataRecord)
                .where(
                    tuple_(
                        SecretVersionMetadataRecord.secret_reference_id,
                        SecretVersionMetadataRecord.version_id,
                    ).in_(secret_version_ids)
                )
                .order_by(
                    SecretVersionMetadataRecord.secret_reference_id,
                    SecretVersionMetadataRecord.version_id,
                )
                .with_for_update()
            )
        )
        if len(versions) != len(secret_version_ids):
            raise RuntimeInvalidTransition("secret_version_not_found")
        return template, references, versions

    @staticmethod
    def _validate_locked_template(
        material: ResolvedRuntimeMaterial,
        template: AdapterTemplateRevisionRecord,
    ) -> None:
        if template.status == "revoked":
            raise RuntimeInvalidTransition("template_revoked")
        if (
            material.root_commit,
            material.backend_commit,
            material.frontend_commit,
            material.strategies_commit,
        ) != (
            template.root_commit,
            template.backend_commit,
            template.frontend_commit,
            template.strategies_commit,
        ):
            raise RuntimeInvalidTransition("component_commit_mismatch")

    @staticmethod
    def _validate_locked_secret_versions(
        instance: RuntimeInstanceRecord,
        resolved_secret_versions: dict[str, str],
        references: tuple[SecretReferenceRecord, ...],
        versions: tuple[SecretVersionMetadataRecord, ...],
    ) -> None:
        for reference in references:
            if reference.status != "active":
                raise RuntimeInvalidTransition("secret_reference_inactive")
            if (
                reference.owner_kind,
                reference.owner_id,
                reference.owner_revision,
            ) != (
                instance.owner_kind,
                instance.owner_id,
                instance.owner_revision,
            ):
                raise RuntimeInvalidTransition("secret_reference_owner_mismatch")
        for version in versions:
            if resolved_secret_versions[version.secret_reference_id] != version.version_id:
                raise RuntimeInvalidTransition("secret_version_not_found")
            if version.status != "active":
                raise RuntimeInvalidTransition("secret_version_inactive")

    @staticmethod
    def _runtime_spec_template_binding(
        session: Session,
        instance: RuntimeInstanceRecord,
    ) -> str:
        runtime_spec = session.get(RuntimeSpecRevisionRecord, instance.runtime_spec_revision_id)
        if runtime_spec is None:
            raise RuntimeDataError("runtime_spec_not_found")
        return runtime_spec.adapter_template_revision_id

    @staticmethod
    def _complete_leased_job(
        job: RuntimeLifecycleJobRecord,
        status: CompletionStatus,
        failure_code: str | None,
        now: datetime,
    ) -> None:
        job.status = status
        job.completed_at = now
        job.failure_code = failure_code
        job.lease_owner = None
        job.lease_expires_at = None

    def _append_supervisor_audit(
        self,
        session: Session,
        job: RuntimeLifecycleJobRecord,
        instance: RuntimeInstanceRecord,
        actor: str,
        adapter_template_revision_id: str | None,
        previous_state: RuntimeInstanceAuditState,
        next_state: RuntimeInstanceAuditState,
        result_code: str,
        now: datetime,
    ) -> None:
        self._append_audit_record(
            session,
            RuntimeAuditEvent(
                actor_type=actor,
                request_id=job.job_id,
                idempotency_key=job.idempotency_key,
                owner_kind=instance.owner_kind,
                owner_id=instance.owner_id,
                owner_revision=instance.owner_revision,
                instance_id=instance.instance_id,
                runtime_spec_revision_id=instance.runtime_spec_revision_id,
                adapter_template_revision_id=adapter_template_revision_id,
                action=RuntimeAuditAction(job.requested_action),
                previous_state=previous_state,
                next_state=next_state,
                result_code=result_code,
            ),
            now,
        )

    def _apply_command(
        self,
        session: Session,
        instance: RuntimeInstanceRecord,
        action: RuntimeAction,
        now: datetime,
    ) -> str:
        has_active_attempt = (
            session.scalar(
                select(RuntimeAttemptRecord.attempt_id)
                .where(
                    RuntimeAttemptRecord.instance_id == instance.instance_id,
                    RuntimeAttemptRecord.status.in_(_ACTIVE_ATTEMPT_STATUSES),
                )
                .limit(1)
            )
            is not None
        )
        if action == RuntimeAction.START:
            return self._apply_start(instance, has_active_attempt)
        if action == RuntimeAction.STOP:
            return self._apply_stop(instance, has_active_attempt)
        if action == RuntimeAction.RETRY:
            return self._apply_retry(instance)
        return self._apply_retire(instance, has_active_attempt, now)

    @staticmethod
    def _apply_start(instance: RuntimeInstanceRecord, has_active_attempt: bool) -> str:
        if instance.desired_state != "stopped" or instance.lifecycle_status not in {
            "registered",
            "stopped",
        }:
            raise RuntimeInvalidTransition("start_requires_stopped")
        if instance.failure_latched:
            raise RuntimeInvalidTransition("start_failure_latched")
        if has_active_attempt:
            raise RuntimeInvalidTransition("start_active_attempt_exists")
        instance.desired_state = "running"
        return "pending"

    @staticmethod
    def _apply_stop(instance: RuntimeInstanceRecord, has_active_attempt: bool) -> str:
        if instance.desired_state == "retired" or instance.lifecycle_status == "retired":
            raise RuntimeInvalidTransition("stop_retired_instance")
        is_no_op = (
            instance.desired_state == "stopped"
            and instance.lifecycle_status in {"registered", "stopped"}
            and not has_active_attempt
        )
        instance.desired_state = "stopped"
        return "succeeded" if is_no_op else "pending"

    @staticmethod
    def _apply_retry(instance: RuntimeInstanceRecord) -> str:
        if instance.desired_state != "running":
            raise RuntimeInvalidTransition("retry_requires_running")
        if instance.lifecycle_status != "failed":
            raise RuntimeInvalidTransition("retry_requires_failed")
        if not instance.failure_latched:
            raise RuntimeInvalidTransition("retry_requires_failure_latch")
        instance.failure_latched = False
        return "pending"

    @staticmethod
    def _apply_retire(
        instance: RuntimeInstanceRecord,
        has_active_attempt: bool,
        now: datetime,
    ) -> str:
        if instance.desired_state != "stopped":
            raise RuntimeInvalidTransition("retire_requires_stopped")
        if instance.lifecycle_status not in {"registered", "stopped", "failed"}:
            raise RuntimeInvalidTransition("retire_requires_terminal")
        if has_active_attempt:
            raise RuntimeInvalidTransition("retire_active_attempt_exists")
        instance.desired_state = "retired"
        instance.lifecycle_status = "retired"
        instance.retired_at = now
        return "succeeded"

    def _mark_reconciliation(
        self,
        session: Session,
        job: RuntimeLifecycleJobRecord,
        actor: str,
        now: datetime,
    ) -> None:
        job.status = "needs_reconciliation"
        job.completed_at = now
        job.failure_code = "stale_lease"
        job.lease_owner = None
        job.lease_expires_at = None
        self._append_job_audit(session, job, actor, "stale_lease", now)

    def _append_job_audit(
        self,
        session: Session,
        job: RuntimeLifecycleJobRecord,
        actor: str,
        result_code: str,
        now: datetime,
    ) -> None:
        instance = self._require_instance(session, job.instance_id)
        state = self._audit_state(instance)
        self._append_audit_record(
            session,
            RuntimeAuditEvent(
                actor_type=actor,
                request_id=job.job_id,
                idempotency_key=job.idempotency_key,
                owner_kind=instance.owner_kind,
                owner_id=instance.owner_id,
                owner_revision=instance.owner_revision,
                instance_id=instance.instance_id,
                runtime_spec_revision_id=instance.runtime_spec_revision_id,
                adapter_template_revision_id=None,
                action=RuntimeAuditAction(job.requested_action),
                previous_state=state,
                next_state=state,
                result_code=result_code,
            ),
            now,
        )

    def _append_audit_record(
        self,
        session: Session,
        event: RuntimeAuditEvent,
        occurred_at: datetime,
    ) -> None:
        session.add(
            RuntimeAuditEventRecord(
                audit_event_id=self._new_id("audit"),
                actor_type=event.actor_type,
                request_id=event.request_id,
                idempotency_key=event.idempotency_key,
                owner_kind=event.owner_kind,
                owner_id=event.owner_id,
                owner_revision=event.owner_revision,
                instance_id=event.instance_id,
                runtime_spec_revision_id=event.runtime_spec_revision_id,
                adapter_template_revision_id=event.adapter_template_revision_id,
                action=event.action,
                previous_state=self._state_json(event.previous_state),
                next_state=self._state_json(event.next_state),
                result_code=event.result_code,
                occurred_at=occurred_at,
                provenance={"source": _AUDIT_SOURCE},
            )
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime_clock_must_be_timezone_aware")
        return value.astimezone(UTC)

    def _new_id(self, prefix: str) -> str:
        return _IDENTIFIER_ADAPTER.validate_python(self._id_factory(prefix))

    @staticmethod
    def _require_instance(session: Session, instance_id: str) -> RuntimeInstanceRecord:
        instance = session.get(RuntimeInstanceRecord, instance_id)
        if instance is None:
            raise RuntimeNotFound("runtime_instance_not_found")
        return instance

    @staticmethod
    def _audit_state(instance: RuntimeInstanceRecord) -> RuntimeInstanceAuditState:
        return RuntimeInstanceAuditState(
            desired_state=instance.desired_state,
            lifecycle_status=instance.lifecycle_status,
            failure_latched=instance.failure_latched,
            optimistic_version=instance.optimistic_version,
        )

    @staticmethod
    def _state_json(state: RuntimeInstanceAuditState | None) -> dict | None:
        return state.model_dump(mode="json") if state is not None else None

    @staticmethod
    def _instance_view(record: RuntimeInstanceRecord) -> RuntimeInstanceView:
        return _registry_view(
            lambda: RuntimeInstanceView(
                instance_id=record.instance_id,
                instance_kind=record.instance_kind,
                owner_ref=RuntimeOwnerRef(
                    owner_kind=record.owner_kind,
                    owner_id=record.owner_id,
                    owner_revision=record.owner_revision,
                ),
                management_mode=RuntimeManagementMode(record.management_mode),
                runtime_spec_revision_id=record.runtime_spec_revision_id,
                environment=record.environment,
                state_allocation_id=record.state_allocation_id,
                desired_state=record.desired_state,
                lifecycle_status=record.lifecycle_status,
                failure_latched=record.failure_latched,
                optimistic_version=record.optimistic_version,
                created_at=_aware_utc(record.created_at),
                retired_at=_aware_utc(record.retired_at),
            )
        )

    @staticmethod
    def _attempt_view(record: RuntimeAttemptRecord) -> RuntimeAttemptView:
        return _registry_view(
            lambda: RuntimeAttemptView(
                attempt_id=record.attempt_id,
                instance_id=record.instance_id,
                attempt_number=record.attempt_number,
                runtime_spec_revision_id=record.runtime_spec_revision_id,
                adapter_template_revision_id=record.adapter_template_revision_id,
                status=RuntimeAttemptStatus(record.status),
                health_result=_health_result_summary(record.health_result),
                started_at=_aware_utc(record.started_at),
                stopped_at=_aware_utc(record.stopped_at),
                exit_code=record.exit_code,
                failure_code=record.failure_code,
            )
        )

    @staticmethod
    def _job_view(record: RuntimeLifecycleJobRecord) -> RuntimeJobView:
        return _registry_view(
            lambda: RuntimeJobView(
                job_id=record.job_id,
                instance_id=record.instance_id,
                requested_action=record.requested_action,
                idempotency_key=record.idempotency_key,
                expected_instance_version=record.expected_instance_version,
                status=RuntimeJobStatus(record.status),
                lease_owner=record.lease_owner,
                lease_expires_at=_aware_utc(record.lease_expires_at),
                requested_at=_aware_utc(record.requested_at),
                started_at=_aware_utc(record.started_at),
                completed_at=_aware_utc(record.completed_at),
                failure_code=record.failure_code,
            )
        )
