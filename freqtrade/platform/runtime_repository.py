from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from freqtrade.platform.runtime_domain import (
    Identifier,
    RuntimeAction,
    RuntimeAttemptStatus,
    RuntimeAttemptView,
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


class RuntimeNotFound(RuntimeError):
    pass


class RuntimeConflict(RuntimeError):
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
    action: RuntimeAction
    previous_state: RuntimeInstanceAuditState | None
    next_state: RuntimeInstanceAuditState | None
    result_code: Identifier


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
                    action=command.action,
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

    def append_audit(self, event: RuntimeAuditEvent) -> None:
        with Session(self._engine) as session, session.begin():
            self._append_audit_record(session, event, self._now())

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
                action=job.requested_action,
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
        return RuntimeInstanceView(
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

    @staticmethod
    def _attempt_view(record: RuntimeAttemptRecord) -> RuntimeAttemptView:
        return RuntimeAttemptView(
            attempt_id=record.attempt_id,
            instance_id=record.instance_id,
            attempt_number=record.attempt_number,
            runtime_spec_revision_id=record.runtime_spec_revision_id,
            adapter_template_revision_id=record.adapter_template_revision_id,
            status=RuntimeAttemptStatus(record.status),
            health_result=record.health_result,
            started_at=_aware_utc(record.started_at),
            stopped_at=_aware_utc(record.stopped_at),
            exit_code=record.exit_code,
            failure_code=record.failure_code,
        )

    @staticmethod
    def _job_view(record: RuntimeLifecycleJobRecord) -> RuntimeJobView:
        return RuntimeJobView(
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
