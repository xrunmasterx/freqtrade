from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session

from freqtrade.markets.catalog import ProductType
from freqtrade.markets.instrument import MarketType
from freqtrade.platform import runtime_service
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_domain import RuntimeLifecycleCommand, RuntimeOwnerRef
from freqtrade.platform.runtime_models import (
    RuntimeAttemptRecord,
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
    RuntimeLifecycleJobRecord,
)
from freqtrade.platform.runtime_repository import RuntimeInvalidTransition, SqlRuntimeRepository
from freqtrade.platform.runtime_spec import (
    RuntimeMarketScope,
    RuntimeSpecPayload,
    RuntimeSpecRevision,
)
from freqtrade.platform.template_models import (
    AdapterTemplateRevisionRecord,
    RuntimeSpecRevisionRecord,
    SecretReferenceRecord,
    SecretVersionMetadataRecord,
    StateAllocationRecord,
)


NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
RUNTIME_SPEC = RuntimeSpecRevision.from_payload(
    RuntimeSpecPayload(
        owner_ref=RuntimeOwnerRef(
            owner_kind="paper_probe",
            owner_id="owner-1",
            owner_revision="owner-revision-1",
        ),
        instance_kind="execution_worker",
        catalog_revision_id="catalog-revision-1",
        market_scope=RuntimeMarketScope(
            market_id=MarketType.DIGITAL_ASSET,
            product_ids=(ProductType.SPOT,),
        ),
        environment="paper",
        adapter_template_revision_id="adapter-template-1",
        template_digest="a" * 64,
        image_policy_id="reviewed-image-v1",
        command_policy_id="fixed-command-v1",
        mount_policy_ids=("runtime-mounts-v1",),
        network_policy_id="private-network-v1",
        health_profile_id="api-ping-v1",
        resource_profile_id="paper-small-v1",
        state_layout_id="fixture-layout-1",
        state_allocation_id="state-allocation-1",
        secret_reference_ids=("exchange",),
        config_blob_commit="1" * 40,
        strategy_commit="4" * 40,
        safety_policy_commit="1" * 40,
        root_commit="1" * 40,
        backend_commit="2" * 40,
        frontend_commit="3" * 40,
        strategies_commit="4" * 40,
        config_blob_digest="5" * 64,
        strategy_digest="6" * 64,
        safety_policy_digest="7" * 64,
    )
)
RUNTIME_SPEC_PAYLOAD_DIGEST = RUNTIME_SPEC.payload_digest
RUNTIME_SPEC_REVISION_ID = RUNTIME_SPEC.runtime_spec_revision_id


class MutableClock:
    def __init__(self) -> None:
        self.now = NOW
        self._sequence: list[datetime] = []

    def __call__(self) -> datetime:
        if self._sequence:
            return self._sequence.pop(0)
        return self.now

    def set_sequence(self, *values: datetime) -> None:
        self._sequence = list(values)


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
            SecretReferenceRecord(
                secret_reference_id="exchange",
                provider_id="local-file-v1",
                secret_class="exchange_credentials",
                logical_name="paper-exchange",
                owner_kind="paper_probe",
                owner_id="owner-1",
                owner_revision="owner-revision-1",
                status="active",
                created_at=NOW,
                retired_at=None,
            ),
        )
    )
    session.flush()
    session.add_all(
        (
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
                canonical_payload=RUNTIME_SPEC.canonical_payload,
                payload_digest=RUNTIME_SPEC_PAYLOAD_DIGEST,
                created_at=NOW,
            ),
            SecretVersionMetadataRecord(
                secret_reference_id="exchange",
                version_id="secret-version-1",
                status="active",
                created_at=NOW,
                activated_at=NOW,
                retired_at=None,
            ),
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


def _resolved_material(
    image_suffix: str = "a",
    *,
    secret_versions: dict[str, str] | None = None,
):
    return runtime_service.ResolvedRuntimeMaterial(
        runtime_spec_revision_id=RUNTIME_SPEC_REVISION_ID,
        adapter_template_revision_id="adapter-template-1",
        state_allocation_id="state-allocation-1",
        resolved_secret_versions=(
            {"exchange": "secret-version-1"}
            if secret_versions is None
            else secret_versions
        ),
        image_id=f"sha256:{image_suffix * 64}",
        root_commit="1" * 40,
        backend_commit="2" * 40,
        frontend_commit="3" * 40,
        strategies_commit="4" * 40,
        project_identity="project-1",
        container_identity=f"container-{image_suffix}",
    )


def _resolved_secret_version_mapping(material: object) -> dict[str, str]:
    resolved = material.resolved_secret_versions
    if isinstance(resolved, dict):
        return dict(resolved)
    return {entry.secret_reference_id: entry.version_id for entry in resolved}


def _database_snapshot(engine: Engine) -> tuple[tuple[tuple[object, ...], ...], ...]:
    with Session(engine) as session:
        instances = session.execute(
            select(
                RuntimeInstanceRecord.instance_id,
                RuntimeInstanceRecord.desired_state,
                RuntimeInstanceRecord.lifecycle_status,
                RuntimeInstanceRecord.failure_latched,
                RuntimeInstanceRecord.optimistic_version,
            ).order_by(RuntimeInstanceRecord.instance_id)
        )
        jobs = session.execute(
            select(
                RuntimeLifecycleJobRecord.job_id,
                RuntimeLifecycleJobRecord.status,
                RuntimeLifecycleJobRecord.lease_owner,
                RuntimeLifecycleJobRecord.lease_expires_at,
                RuntimeLifecycleJobRecord.completed_at,
                RuntimeLifecycleJobRecord.failure_code,
            ).order_by(RuntimeLifecycleJobRecord.job_id)
        )
        attempts = session.execute(
            select(
                RuntimeAttemptRecord.attempt_id,
                RuntimeAttemptRecord.status,
                RuntimeAttemptRecord.health_result,
                RuntimeAttemptRecord.stopped_at,
                RuntimeAttemptRecord.exit_code,
                RuntimeAttemptRecord.failure_code,
                RuntimeAttemptRecord.resolved_secret_versions,
                RuntimeAttemptRecord.image_id,
            ).order_by(RuntimeAttemptRecord.attempt_id)
        )
        audits = session.execute(
            select(
                RuntimeAuditEventRecord.audit_event_id,
                RuntimeAuditEventRecord.result_code,
                RuntimeAuditEventRecord.previous_state,
                RuntimeAuditEventRecord.next_state,
            ).order_by(RuntimeAuditEventRecord.audit_event_id)
        )
        return tuple(
            tuple(tuple(row) for row in result)
            for result in (instances, jobs, attempts, audits)
        )


def _set_runtime_spec_secret_references(engine: Engine, reference_ids: tuple[str, ...]) -> None:
    with Session(engine) as session, session.begin():
        record = session.get(RuntimeSpecRevisionRecord, RUNTIME_SPEC_REVISION_ID)
        assert record is not None
        payload = RuntimeSpecPayload.model_validate_json(record.canonical_payload)
        changed = RuntimeSpecRevision.from_payload(
            payload.model_copy(update={"secret_reference_ids": reference_ids})
        )
        record.canonical_payload = changed.canonical_payload


def _seed_other_secret_version(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            SecretReferenceRecord(
                secret_reference_id="other-secret",
                provider_id="local-file-v1",
                secret_class="other_credentials",
                logical_name="paper-other",
                owner_kind="paper_probe",
                owner_id="owner-1",
                owner_revision="owner-revision-1",
                status="active",
                created_at=NOW,
                retired_at=None,
            )
        )
        session.flush()
        session.add(
            SecretVersionMetadataRecord(
                secret_reference_id="other-secret",
                version_id="other-version-1",
                status="active",
                created_at=NOW,
                activated_at=NOW,
                retired_at=None,
            )
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
    assert record.resolved_secret_versions == _resolved_secret_version_mapping(material)
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


def test_resolved_secret_versions_are_deeply_immutable() -> None:
    material = _resolved_material()
    resolved_versions = material.resolved_secret_versions

    with pytest.raises(TypeError):
        resolved_versions[0] = resolved_versions[0]
    with pytest.raises(ValidationError):
        resolved_versions[0].version_id = "tampered-version"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_id", "C:/runtime/secret"),
        ("root_commit", "not-a-commit"),
        ("container_identity", "invalid/container"),
    ],
)
def test_begin_attempt_revalidates_unsafe_model_copy_before_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    field: str,
    value: str,
) -> None:
    material = _resolved_material().model_copy(update={field: value})
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_resolved_material$"):
        repository.begin_attempt(running_job.job_id, material)

    assert _database_snapshot(engine) == before


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("missing", "secret_reference_set_mismatch"),
        ("extra", "secret_reference_set_mismatch"),
        ("unknown", "secret_reference_not_found"),
        ("wrong_reference", "secret_version_not_found"),
        ("inactive_reference", "secret_reference_inactive"),
        ("inactive_version", "secret_version_inactive"),
        ("owner_mismatch", "secret_reference_owner_mismatch"),
    ],
)
def test_begin_attempt_validates_exact_secret_version_provenance_before_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    case: str,
    error: str,
) -> None:
    secret_versions = {"exchange": "secret-version-1"}
    if case == "missing":
        secret_versions = {}
    elif case == "extra":
        secret_versions["extra-secret"] = "extra-version-1"
    elif case == "unknown":
        _set_runtime_spec_secret_references(engine, ("unknown-secret",))
        secret_versions = {"unknown-secret": "unknown-version-1"}
    elif case == "wrong_reference":
        _seed_other_secret_version(engine)
        secret_versions["exchange"] = "other-version-1"
    elif case in {"inactive_reference", "inactive_version", "owner_mismatch"}:
        with Session(engine) as session, session.begin():
            if case == "inactive_version":
                version = session.get(
                    SecretVersionMetadataRecord,
                    ("exchange", "secret-version-1"),
                )
                assert version is not None
                version.status = "retired"
                version.retired_at = NOW
            else:
                reference = session.get(SecretReferenceRecord, "exchange")
                assert reference is not None
                if case == "inactive_reference":
                    reference.status = "disabled"
                else:
                    reference.owner_id = "other-owner"

    before = _database_snapshot(engine)
    with pytest.raises(RuntimeInvalidTransition, match=rf"^{error}$"):
        repository.begin_attempt(
            running_job.job_id,
            _resolved_material(secret_versions=secret_versions),
        )

    assert _database_snapshot(engine) == before


@pytest.mark.parametrize("attempt_status", ["launching", "healthy"])
def test_latch_failure_rejects_active_attempt_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    attempt_status: str,
) -> None:
    attempt = repository.begin_attempt(running_job.job_id, _resolved_material())
    job_id = running_job.job_id
    if attempt_status == "healthy":
        repository.record_healthy(running_job.job_id, attempt.attempt_id)
        repository.create_job(_command("stop", "stop-active", version=1), "operator_cli")
        stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
        assert stop_job is not None
        job_id = stop_job.job_id

    before = _database_snapshot(engine)
    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^active_attempt_requires_explicit_failure$",
    ):
        repository.latch_failure(job_id, "container_missing")

    assert _database_snapshot(engine) == before


@pytest.mark.parametrize(
    "operation",
    [
        "begin_attempt",
        "record_healthy",
        "record_failed",
        "record_stopped",
        "renew_lease",
        "latch_failure",
    ],
)
def test_supervisor_transitions_validate_lease_with_post_lock_time(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
    operation: str,
) -> None:
    job_id = running_job.job_id
    attempt_id: str | None = None
    if operation in {"record_healthy", "record_failed", "record_stopped"}:
        attempt = repository.begin_attempt(running_job.job_id, _resolved_material())
        attempt_id = attempt.attempt_id
        if operation == "record_stopped":
            repository.record_healthy(running_job.job_id, attempt.attempt_id)
            repository.create_job(_command("stop", "stop-expiry", version=1), "operator_cli")
            stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
            assert stop_job is not None
            job_id = stop_job.job_id

    def invoke() -> object:
        if operation == "begin_attempt":
            return repository.begin_attempt(job_id, _resolved_material())
        if operation == "record_healthy":
            return repository.record_healthy(job_id, attempt_id)
        if operation == "record_failed":
            return repository.record_failed(job_id, attempt_id, "health_timeout")
        if operation == "record_stopped":
            return repository.record_stopped(job_id, attempt_id, exit_code=0)
        if operation == "renew_lease":
            return repository.renew_lease(job_id, "supervisor-1", lease_seconds=30)
        return repository.latch_failure(job_id, "container_missing")

    before = _database_snapshot(engine)
    clock.set_sequence(NOW, NOW + timedelta(seconds=31))
    lock_query_seen = False

    def consume_pre_lock_time(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal lock_query_seen
        if not lock_query_seen and "FROM runtime_lifecycle_jobs" in statement:
            lock_query_seen = True
            clock()

    event.listen(engine, "before_cursor_execute", consume_pre_lock_time)
    try:
        with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
            invoke()
    finally:
        event.remove(engine, "before_cursor_execute", consume_pre_lock_time)

    assert lock_query_seen is True
    assert _database_snapshot(engine) == before


@pytest.mark.parametrize(
    ("status", "failure_code"),
    [("succeeded", None), ("failed", "generic_failure")],
)
def test_complete_job_cannot_bypass_running_attempt_transition(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    status: str,
    failure_code: str | None,
) -> None:
    repository.begin_attempt(running_job.job_id, _resolved_material())
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^attempt_transition_required$"):
        repository.complete_job(running_job.job_id, status, failure_code)

    assert _database_snapshot(engine) == before
