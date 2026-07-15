from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from freqtrade.platform import runtime_service
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_domain import RuntimeLifecycleCommand
from freqtrade.platform.runtime_models import (
    RuntimeAttemptRecord,
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
    RuntimeLifecycleJobRecord,
)
from freqtrade.platform.runtime_repository import RuntimeInvalidTransition, SqlRuntimeRepository
from freqtrade.platform.template_models import (
    AdapterTemplateRevisionRecord,
    RuntimeSpecRevisionRecord,
    StateAllocationRecord,
)


NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
RUNTIME_SPEC_PAYLOAD_DIGEST = "b" * 64
RUNTIME_SPEC_REVISION_ID = f"runtime-spec-{RUNTIME_SPEC_PAYLOAD_DIGEST}"


class MutableClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class SequentialIds:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.count}"


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def engine() -> Iterator[Engine]:
    value = create_engine("sqlite+pysqlite:///:memory:")
    PlatformBase.metadata.create_all(value)
    with value.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_runtime_attempt_active")
        connection.exec_driver_sql("DROP INDEX uq_runtime_job_active")
    try:
        yield value
    finally:
        value.dispose()


@pytest.fixture
def repository(engine: Engine, clock: MutableClock) -> SqlRuntimeRepository:
    _seed_instance(engine)
    return SqlRuntimeRepository(engine, clock=clock, id_factory=SequentialIds())


@pytest.fixture
def running_job(repository: SqlRuntimeRepository):
    repository.create_job(_command("start", "start-1"), "operator_cli")
    job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert job is not None
    return job


def _seed_runtime_parent_chain(session: Session) -> None:
    if session.get(RuntimeSpecRevisionRecord, RUNTIME_SPEC_REVISION_ID) is not None:
        return
    session.add_all(
        (
            CatalogRevisionRecord(
                revision_id="catalog-revision-1",
                payload={"schema_version": 1},
                created_at=NOW,
            ),
            AdapterTemplateRevisionRecord(
                adapter_template_revision_id="adapter-template-1",
                template_id="adapter-template-1",
                semantic_version="1.0.0",
                canonical_payload="{}",
                payload_digest="a" * 64,
                source_commit="1" * 40,
                root_commit="1" * 40,
                backend_commit="2" * 40,
                frontend_commit="3" * 40,
                strategies_commit="4" * 40,
                status="active",
                published_by="platform-test",
                published_at=NOW,
                deprecated_at=None,
                revoked_at=None,
            ),
            StateAllocationRecord(
                state_allocation_id="state-allocation-1",
                instance_id="fixture-parent-instance",
                layout_id="fixture-layout-1",
                provider_id="managed-local-v1",
                relative_path="ft_userdata/runtime/instances/fixture-parent-instance",
                kind="fresh",
                status="ready",
                generation=1,
                restore_source_bundle_id=None,
                created_at=NOW,
                ready_at=NOW,
                retired_at=None,
            ),
        )
    )
    session.flush()
    session.add(
        RuntimeSpecRevisionRecord(
            runtime_spec_revision_id=RUNTIME_SPEC_REVISION_ID,
            owner_kind="paper_probe",
            owner_id="owner-1",
            owner_revision="owner-revision-1",
            instance_kind="execution_worker",
            catalog_revision_id="catalog-revision-1",
            environment="paper",
            adapter_template_revision_id="adapter-template-1",
            state_allocation_id="state-allocation-1",
            canonical_payload="{}",
            payload_digest=RUNTIME_SPEC_PAYLOAD_DIGEST,
            created_at=NOW,
        )
    )
    session.flush()


def _seed_instance(engine: Engine, instance_id: str = "instance-1") -> None:
    with Session(engine) as session, session.begin():
        _seed_runtime_parent_chain(session)
        session.add(
            RuntimeInstanceRecord(
                instance_id=instance_id,
                instance_kind="execution_worker",
                owner_kind="paper_probe",
                owner_id="owner-1",
                owner_revision="owner-revision-1",
                management_mode="supervisor",
                runtime_spec_revision_id=RUNTIME_SPEC_REVISION_ID,
                environment="paper",
                state_allocation_id="state-allocation-1",
                desired_state="stopped",
                lifecycle_status="registered",
                failure_latched=False,
                optimistic_version=0,
                created_at=NOW,
                retired_at=None,
            )
        )


def _command(
    action: str,
    key: str,
    *,
    version: int = 0,
    instance_id: str = "instance-1",
) -> RuntimeLifecycleCommand:
    return RuntimeLifecycleCommand(
        instance_id=instance_id,
        action=action,
        idempotency_key=key,
        expected_instance_version=version,
    )


def _resolved_material(image_suffix: str = "a"):
    return runtime_service.ResolvedRuntimeMaterial(
        runtime_spec_revision_id=RUNTIME_SPEC_REVISION_ID,
        adapter_template_revision_id="adapter-template-1",
        state_allocation_id="state-allocation-1",
        resolved_secret_versions={"exchange": "secret-version-1"},
        image_id=f"sha256:{image_suffix * 64}",
        root_commit="1" * 40,
        backend_commit="2" * 40,
        frontend_commit="3" * 40,
        strategies_commit="4" * 40,
        project_identity="project-1",
        container_identity=f"container-{image_suffix}",
    )


def _persisted_records(
    engine: Engine,
    job_id: str,
    attempt_id: str,
) -> tuple[RuntimeInstanceRecord, RuntimeLifecycleJobRecord, RuntimeAttemptRecord]:
    with Session(engine) as session:
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        job = session.get(RuntimeLifecycleJobRecord, job_id)
        attempt = session.get(RuntimeAttemptRecord, attempt_id)
        assert instance is not None
        assert job is not None
        assert attempt is not None
        session.expunge_all()
        return instance, job, attempt


def test_supervisor_repository_contract_exposes_all_transition_methods(
    repository: SqlRuntimeRepository,
) -> None:
    for method_name in (
        "begin_attempt",
        "record_healthy",
        "record_failed",
        "record_stopped",
        "renew_lease",
        "latch_failure",
    ):
        assert callable(getattr(repository, method_name))


def test_resolved_material_is_closed_typed_and_contains_no_path_or_secret_value_fields() -> None:
    material = _resolved_material()

    assert set(material.model_dump()) == {
        "runtime_spec_revision_id",
        "adapter_template_revision_id",
        "state_allocation_id",
        "resolved_secret_versions",
        "image_id",
        "root_commit",
        "backend_commit",
        "frontend_commit",
        "strategies_commit",
        "project_identity",
        "container_identity",
    }
    with pytest.raises(ValidationError):
        runtime_service.ResolvedRuntimeMaterial.model_validate(
            {**material.model_dump(), "secret_path": "C:/secrets/exchange"}
        )
    with pytest.raises(ValidationError):
        runtime_service.ResolvedRuntimeMaterial.model_validate(
            {**material.model_dump(), "secret_value": "credential"}
        )
    with pytest.raises(ValidationError):
        runtime_service.ResolvedRuntimeMaterial.model_validate(
            {**material.model_dump(), "image_id": "C:/runtime/secret"}
        )


def test_begin_attempt_creates_monotonic_append_only_attempt(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    first_material = _resolved_material("a")
    first = repository.begin_attempt(running_job.job_id, first_material)
    repository.record_healthy(running_job.job_id, first.attempt_id)

    repository.create_job(_command("stop", "stop-1", version=1), "operator_cli")
    stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert stop_job is not None
    repository.record_stopped(stop_job.job_id, first.attempt_id, exit_code=0)

    repository.create_job(_command("start", "start-2", version=2), "operator_cli")
    next_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert next_job is not None
    second = repository.begin_attempt(next_job.job_id, _resolved_material("b"))

    assert second.attempt_number == first.attempt_number + 1
    with Session(engine) as session:
        persisted = session.scalars(
            select(RuntimeAttemptRecord).order_by(RuntimeAttemptRecord.attempt_number)
        ).all()
    assert len(persisted) == 2
    assert persisted[0].image_id == first_material.image_id
    assert persisted[0].status == "stopped"
    assert persisted[1].image_id == f"sha256:{'b' * 64}"


def test_begin_attempt_persists_exact_material_and_transitions_job_instance_and_audit(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    material = _resolved_material()

    attempt = repository.begin_attempt(running_job.job_id, material)
    instance, job, record = _persisted_records(engine, running_job.job_id, attempt.attempt_id)

    assert attempt.status == "launching"
    assert instance.lifecycle_status == "starting"
    assert job.status == "running"
    assert job.lease_owner == "supervisor-1"
    assert record.runtime_spec_revision_id == material.runtime_spec_revision_id
    assert record.adapter_template_revision_id == material.adapter_template_revision_id
    assert record.resolved_secret_versions == material.resolved_secret_versions
    assert record.image_id == material.image_id
    assert record.root_commit == material.root_commit
    assert record.backend_commit == material.backend_commit
    assert record.frontend_commit == material.frontend_commit
    assert record.strategies_commit == material.strategies_commit
    assert record.project_identity == material.project_identity
    assert record.container_identity == material.container_identity
    with Session(engine) as session:
        audit = session.scalars(
            select(RuntimeAuditEventRecord).order_by(RuntimeAuditEventRecord.occurred_at)
        ).all()[-1]
    assert audit.request_id == running_job.job_id
    assert audit.adapter_template_revision_id == "adapter-template-1"
    assert audit.result_code == "attempt_started"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("runtime_spec_revision_id", "runtime-spec-wrong", "runtime_spec_mismatch"),
        ("adapter_template_revision_id", "adapter-template-wrong", "template_mismatch"),
        ("state_allocation_id", "state-allocation-wrong", "state_allocation_mismatch"),
        ("root_commit", "9" * 40, "component_commit_mismatch"),
    ],
)
def test_begin_attempt_rejects_material_identity_mismatch_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    field: str,
    value: str,
    error: str,
) -> None:
    material = _resolved_material().model_copy(update={field: value})

    with pytest.raises(RuntimeInvalidTransition, match=rf"^{error}$"):
        repository.begin_attempt(running_job.job_id, material)

    assert repository.list_attempts("instance-1") == ()
    assert repository.get_instance("instance-1").lifecycle_status == "registered"
    with Session(engine) as session:
        job = session.get(RuntimeLifecycleJobRecord, running_job.job_id)
        assert job is not None
        assert job.status == "claimed"


def test_begin_attempt_requires_current_leased_start_or_retry_job(
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    clock.now += timedelta(seconds=31)

    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
        repository.begin_attempt(running_job.job_id, _resolved_material())

    assert repository.list_attempts("instance-1") == ()


def test_record_healthy_binds_job_and_attempt_and_completes_atomically(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = repository.begin_attempt(running_job.job_id, _resolved_material())

    healthy = repository.record_healthy(running_job.job_id, attempt.attempt_id)
    instance, job, record = _persisted_records(engine, running_job.job_id, attempt.attempt_id)

    assert healthy.status == "healthy"
    assert healthy.health_result == "healthy"
    assert instance.lifecycle_status == "healthy"
    assert instance.failure_latched is False
    assert job.status == "succeeded"
    assert job.lease_owner is None
    assert job.lease_expires_at is None
    assert record.health_result == {"result_code": "healthy"}


def test_transition_rejects_attempt_from_another_instance_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    first = repository.begin_attempt(running_job.job_id, _resolved_material("a"))
    _seed_instance(engine, "instance-2")
    repository.create_job(
        _command("start", "start-2", instance_id="instance-2"),
        "operator_cli",
    )
    other_job = repository.claim_next_job("supervisor-2", lease_seconds=30)
    assert other_job is not None
    other = repository.begin_attempt(
        other_job.job_id,
        _resolved_material("b"),
    )

    with pytest.raises(RuntimeInvalidTransition, match=r"^job_attempt_instance_mismatch$"):
        repository.record_healthy(running_job.job_id, other.attempt_id)

    assert repository.list_attempts("instance-1")[0] == first
    assert repository.list_attempts("instance-2")[0] == other
    assert repository.list_jobs("instance-1")[0].status == "running"


def test_failed_attempt_latches_without_queuing_retry(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = repository.begin_attempt(running_job.job_id, _resolved_material())

    failed = repository.record_failed(
        running_job.job_id,
        attempt.attempt_id,
        "health_timeout",
    )
    instance, job, record = _persisted_records(engine, running_job.job_id, attempt.attempt_id)

    assert failed.status == "failed"
    assert failed.failure_code == "health_timeout"
    assert instance.lifecycle_status == "failed"
    assert instance.failure_latched is True
    assert job.status == "failed"
    assert job.failure_code == "health_timeout"
    assert record.stopped_at == NOW.replace(tzinfo=None)
    assert all(job.status != "pending" for job in repository.list_jobs(attempt.instance_id))


def test_record_stopped_requires_explicit_stop_job_and_transitions_all_records(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = repository.begin_attempt(running_job.job_id, _resolved_material())
    repository.record_healthy(running_job.job_id, attempt.attempt_id)
    repository.create_job(_command("stop", "stop-1", version=1), "operator_cli")
    stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert stop_job is not None

    stopped = repository.record_stopped(stop_job.job_id, attempt.attempt_id, exit_code=0)
    instance, job, record = _persisted_records(engine, stop_job.job_id, attempt.attempt_id)

    assert stopped.status == "stopped"
    assert stopped.exit_code == 0
    assert instance.desired_state == "stopped"
    assert instance.lifecycle_status == "stopped"
    assert job.status == "succeeded"
    assert record.stopped_at == NOW.replace(tzinfo=None)


def test_latch_failure_handles_current_job_without_creating_or_changing_attempt(
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    failed_job = repository.latch_failure(running_job.job_id, "container_missing")

    instance = repository.get_instance("instance-1")
    assert failed_job.status == "failed"
    assert failed_job.failure_code == "container_missing"
    assert instance.lifecycle_status == "failed"
    assert instance.failure_latched is True
    assert repository.list_attempts("instance-1") == ()


def test_renew_lease_is_bounded_and_owner_safe(
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    renewed = repository.renew_lease(running_job.job_id, "supervisor-1", lease_seconds=60)

    assert renewed.lease_owner == "supervisor-1"
    assert renewed.lease_expires_at == NOW + timedelta(seconds=60)
    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_owner_mismatch$"):
        repository.renew_lease(running_job.job_id, "supervisor-2", lease_seconds=60)
    for invalid_seconds in (0, 3601, True):
        with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_lease_seconds$"):
            repository.renew_lease(
                running_job.job_id,
                "supervisor-1",
                lease_seconds=invalid_seconds,
            )
    clock.now += timedelta(seconds=61)
    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
        repository.renew_lease(running_job.job_id, "supervisor-1", lease_seconds=60)


def test_audit_failure_rolls_back_healthy_attempt_job_and_instance(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = repository.begin_attempt(running_job.job_id, _resolved_material())

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected_audit_failure")

    monkeypatch.setattr(repository, "_append_audit_record", fail_audit)
    with pytest.raises(RuntimeError, match=r"^injected_audit_failure$"):
        repository.record_healthy(running_job.job_id, attempt.attempt_id)

    instance, job, record = _persisted_records(engine, running_job.job_id, attempt.attempt_id)
    assert instance.lifecycle_status == "starting"
    assert job.status == "running"
    assert job.lease_owner == "supervisor-1"
    assert record.status == "launching"
    assert record.health_result is None
