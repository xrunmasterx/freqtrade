import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from freqtrade.markets.catalog import ProductType
from freqtrade.markets.instrument import MarketType
from freqtrade.platform import runtime_service
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_domain import (
    RuntimeAttemptView,
    RuntimeJobView,
    RuntimeLifecycleCommand,
    RuntimeOwnerRef,
)
from freqtrade.platform.runtime_models import (
    RuntimeAttemptRecord,
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
    RuntimeLifecycleJobRecord,
)
from freqtrade.platform.runtime_repository import (
    RuntimeDataError,
    RuntimeInvalidTransition,
    SqlRuntimeRepository,
)
from freqtrade.platform.runtime_spec import (
    RuntimeMarketScope,
    RuntimeSpecPayload,
    RuntimeSpecRevision,
)
from freqtrade.platform.template_domain import AdapterTemplate
from freqtrade.platform.template_models import (
    AdapterTemplateRevisionRecord,
    RuntimeSpecRevisionRecord,
    SecretReferenceRecord,
    SecretVersionMetadataRecord,
    StateAllocationRecord,
)
from freqtrade.platform.template_repository import (
    CommittedTemplatePublication,
    SqlTemplateRepository,
)


BACKEND_ROOT = Path(__file__).parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic-platform.ini"
NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
TEMPLATE = AdapterTemplate(
    template_id="adapter-template-1",
    semantic_version="1.0.0",
    allowed_instance_kinds=("execution_worker",),
    allowed_owner_kinds=("paper_probe",),
    allowed_environments=("paper",),
    image_policy_id="reviewed-image-v1",
    command_policy_id="fixed-command-v1",
    mount_policy_ids=("runtime-mounts-v1",),
    network_policy_id="private-network-v1",
    health_profile_id="api-ping-v1",
    resource_profile_id="paper-small-v1",
    secret_classes=("exchange_credentials",),
    state_layout_id="fixture-layout-1",
)
TEMPLATE_CANONICAL_PAYLOAD = json.dumps(
    {"schema_version": 1, **TEMPLATE.model_dump(mode="json")},
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
) + "\n"
TEMPLATE_PUBLICATION = CommittedTemplatePublication(
    template=TEMPLATE,
    canonical_payload=TEMPLATE_CANONICAL_PAYLOAD,
    payload_digest=hashlib.sha256(TEMPLATE_CANONICAL_PAYLOAD.encode()).hexdigest(),
    source_commit="1" * 40,
    root_commit="1" * 40,
    backend_commit="2" * 40,
    frontend_commit="3" * 40,
    strategies_commit="4" * 40,
)
TEMPLATE_REVISION_ID = f"template-{TEMPLATE_PUBLICATION.payload_digest}"
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
        adapter_template_revision_id=TEMPLATE_REVISION_ID,
        template_digest=TEMPLATE_PUBLICATION.payload_digest,
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
def postgres_engine(postgres_url: str) -> Iterator[Engine]:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    command.upgrade(config, "head")
    value = create_engine(postgres_url)
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
                adapter_template_revision_id=TEMPLATE_REVISION_ID,
                template_id=TEMPLATE.template_id,
                semantic_version=TEMPLATE.semantic_version,
                canonical_payload=TEMPLATE_PUBLICATION.canonical_payload,
                payload_digest=TEMPLATE_PUBLICATION.payload_digest,
                source_commit=TEMPLATE_PUBLICATION.source_commit,
                root_commit=TEMPLATE_PUBLICATION.root_commit,
                backend_commit=TEMPLATE_PUBLICATION.backend_commit,
                frontend_commit=TEMPLATE_PUBLICATION.frontend_commit,
                strategies_commit=TEMPLATE_PUBLICATION.strategies_commit,
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
                adapter_template_revision_id=TEMPLATE_REVISION_ID,
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
        adapter_template_revision_id=TEMPLATE_REVISION_ID,
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


def _begin_attempt(
    repository: SqlRuntimeRepository,
    job_id: str,
    material: runtime_service.ResolvedRuntimeMaterial | None = None,
    *,
    lease_owner: str = "supervisor-1",
    lease_generation: int = 1,
) -> RuntimeAttemptView:
    attempt_id = repository.prepare_attempt_id(job_id, lease_owner, lease_generation)
    return repository.begin_attempt(
        job_id,
        attempt_id,
        _resolved_material() if material is None else material,
        lease_owner,
        lease_generation,
    )


def _lease_args(job: RuntimeJobView) -> dict[str, object]:
    assert job.lease_owner is not None
    return {
        "lease_owner": job.lease_owner,
        "lease_generation": job.lease_generation,
    }


def _complete_health_probe(
    repository: SqlRuntimeRepository,
    job: RuntimeJobView,
    attempt_id: str,
    result_code: str = "health_probe_healthy",
) -> None:
    reservation = repository.reserve_health_probe(
        job.job_id,
        attempt_id,
        "api-ping-v1",
        "8" * 64,
        NOW + timedelta(seconds=20),
        NOW,
        **_lease_args(job),
    )
    repository.record_health_observation(
        job.job_id,
        attempt_id,
        result_code,
        reservation.attempts,
        None if result_code == "health_probe_healthy" else result_code,
        **_lease_args(job),
    )


def _record_healthy(
    repository: SqlRuntimeRepository,
    job: RuntimeJobView,
    attempt_id: str,
) -> RuntimeAttemptView:
    _complete_health_probe(repository, job, attempt_id)
    return repository.record_healthy(job.job_id, attempt_id, **_lease_args(job))


def _database_snapshot(engine: Engine) -> tuple[tuple[tuple[object, ...], ...], ...]:
    with Session(engine) as session:
        instances = session.execute(
            select(*RuntimeInstanceRecord.__table__.columns).order_by(
                RuntimeInstanceRecord.instance_id
            )
        )
        jobs = session.execute(
            select(*RuntimeLifecycleJobRecord.__table__.columns).order_by(
                RuntimeLifecycleJobRecord.job_id
            )
        )
        attempts = session.execute(
            select(*RuntimeAttemptRecord.__table__.columns).order_by(
                RuntimeAttemptRecord.attempt_id
            )
        )
        audits = session.execute(
            select(*RuntimeAuditEventRecord.__table__.columns).order_by(
                RuntimeAuditEventRecord.audit_event_id
            )
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
        "prepare_attempt_id",
        "begin_attempt",
        "get_latest_attempt_material",
        "assert_current_lease",
        "reserve_health_probe",
        "record_health_observation",
        "record_reconciliation_blocked",
        "reclaim_reconciliation_job",
        "record_healthy",
        "record_failed",
        "record_stopped",
        "renew_lease",
        "latch_failure",
    ):
        assert callable(getattr(repository, method_name))


def test_prepare_attempt_id_returns_repository_identity_without_database_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    before = _database_snapshot(engine)

    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))

    assert attempt_id.startswith("attempt-")
    assert _database_snapshot(engine) == before


def test_claim_grants_lease_from_post_lock_time(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
) -> None:
    repository.create_job(_command("start", "post-lock-claim"), "operator_cli")
    lock_query_seen = False

    def consume_time_while_waiting_for_lock(
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
            clock.now += timedelta(seconds=5)

    event.listen(engine, "before_cursor_execute", consume_time_while_waiting_for_lock)
    try:
        claimed = repository.claim_next_job("supervisor-1", lease_seconds=30)
    finally:
        event.remove(engine, "before_cursor_execute", consume_time_while_waiting_for_lock)

    assert claimed is not None
    assert lock_query_seen is True
    assert claimed.lease_expires_at == NOW + timedelta(seconds=35)


def test_prepare_attempt_id_requires_current_lease_without_database_mutation(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    before = _database_snapshot(engine)
    clock.now += timedelta(seconds=31)

    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
        repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))

    assert _database_snapshot(engine) == before


def test_prepare_attempt_id_requires_start_or_retry_job_without_database_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    _record_healthy(repository, running_job, attempt.attempt_id)
    repository.create_job(_command("stop", "stop-prepare", version=1), "operator_cli")
    stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert stop_job is not None
    before = _database_snapshot(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^attempt_requires_start_or_retry_job$",
    ):
        repository.prepare_attempt_id(stop_job.job_id, **_lease_args(stop_job))

    assert _database_snapshot(engine) == before


def test_prepare_attempt_id_rejects_active_attempt_without_database_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    _begin_attempt(repository, running_job.job_id)
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^active_attempt_exists$"):
        repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))

    assert _database_snapshot(engine) == before


def test_begin_attempt_persists_exact_prepared_identity(
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))

    attempt = repository.begin_attempt(
        running_job.job_id,
        attempt_id,
        _resolved_material(),
        **_lease_args(running_job),
    )

    assert attempt.attempt_id == attempt_id


def test_begin_attempt_rechecks_active_attempt_after_candidate_preparation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    first_attempt_id = repository.prepare_attempt_id(
        running_job.job_id, **_lease_args(running_job)
    )
    second_attempt_id = repository.prepare_attempt_id(
        running_job.job_id, **_lease_args(running_job)
    )
    repository.begin_attempt(
        running_job.job_id,
        first_attempt_id,
        _resolved_material(),
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^active_attempt_exists$"):
        repository.begin_attempt(
            running_job.job_id,
            second_attempt_id,
            _resolved_material(),
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_latest_attempt_material_is_closed_deeply_immutable_and_secret_safe(
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    assert repository.get_latest_attempt_material("instance-1") is None
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))
    repository.begin_attempt(
        running_job.job_id,
        attempt_id,
        _resolved_material(),
        **_lease_args(running_job),
    )

    recovery = repository.get_latest_attempt_material("instance-1")

    assert recovery is not None
    assert recovery.attempt_id == attempt_id
    assert recovery.status == "launching"
    assert recovery.started_at == NOW
    assert recovery.health_result is None
    assert recovery.runtime_spec_payload_digest == RUNTIME_SPEC_PAYLOAD_DIGEST
    assert recovery.resolved_material == _resolved_material()
    dumped = recovery.model_dump(mode="json")
    serialized = json.dumps(dumped)
    assert set(dumped) == {
        "attempt_id",
        "status",
        "started_at",
        "health_result",
        "runtime_spec_payload_digest",
        "resolved_material",
    }
    assert "secret_value" not in serialized
    assert "secret_path" not in serialized
    assert "lease_owner" not in serialized
    with pytest.raises(ValidationError):
        recovery.status = "failed"
    with pytest.raises(ValidationError):
        recovery.resolved_material.resolved_secret_versions[0].version_id = "tampered"


def test_health_observation_is_durable_monotonic_and_does_not_finish_or_retry(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    deadline = NOW + timedelta(seconds=20)
    first_reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        deadline,
        NOW,
        **_lease_args(running_job),
    )
    reserved_recovery = repository.get_latest_attempt_material("instance-1")
    assert reserved_recovery is not None
    assert reserved_recovery.health_result == first_reservation
    assert first_reservation.attempts == 1
    assert first_reservation.result_code == "health_probe_reserved"

    first = repository.record_health_observation(
        running_job.job_id,
        attempt.attempt_id,
        "health_probe_unhealthy",
        attempts=1,
        last_failure_code="connection_refused",
        **_lease_args(running_job),
    )
    second_reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        deadline,
        NOW + timedelta(seconds=1),
        **_lease_args(running_job),
    )
    second = repository.record_health_observation(
        running_job.job_id,
        attempt.attempt_id,
        "health_probe_unhealthy",
        attempts=2,
        last_failure_code="connection_refused",
        **_lease_args(running_job),
    )

    instance, job, record = _persisted_records(engine, running_job.job_id, attempt.attempt_id)
    recovery = repository.get_latest_attempt_material("instance-1")
    assert first.status == "launching"
    assert second.status == "launching"
    assert instance.lifecycle_status == "starting"
    assert instance.failure_latched is False
    assert job.status == "running"
    assert job.lease_owner == "supervisor-1"
    assert job.lease_generation == 1
    assert job.lease_expires_at == (NOW + timedelta(seconds=30)).replace(tzinfo=None)
    assert record.health_result == {
        "profile_id": "api-ping-v1",
        "profile_digest": "8" * 64,
        "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
        "next_probe_not_before": (NOW + timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "result_code": "health_probe_unhealthy",
        "attempts": 2,
        "last_failure_code": "connection_refused",
    }
    assert recovery is not None
    assert recovery.started_at == NOW
    assert recovery.health_result is not None
    assert recovery.health_result.result_code == "health_probe_unhealthy"
    assert recovery.health_result.attempts == 2
    assert second_reservation.attempts == 2
    assert recovery.health_result.last_failure_code == "connection_refused"
    assert all(item.status != "pending" for item in repository.list_jobs("instance-1"))
    with pytest.raises(ValidationError):
        recovery.health_result.attempts = 0


def test_health_probe_schedule_cannot_move_backwards_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    deadline = NOW + timedelta(seconds=20)
    reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        deadline,
        NOW + timedelta(seconds=2),
        **_lease_args(running_job),
    )
    repository.record_health_observation(
        running_job.job_id,
        attempt.attempt_id,
        "health_probe_unhealthy",
        attempts=reservation.attempts,
        last_failure_code="connection_refused",
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^health_probe_schedule_regression$",
    ):
        repository.reserve_health_probe(
            running_job.job_id,
            attempt.attempt_id,
            "api-ping-v1",
            "8" * 64,
            deadline,
            NOW + timedelta(seconds=1),
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_health_probe_schedule_accepts_exact_deadline(
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    deadline = NOW + timedelta(seconds=20)

    reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        deadline,
        deadline,
        **_lease_args(running_job),
    )

    assert reservation.next_probe_not_before == deadline


def test_health_probe_schedule_rejects_after_deadline_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    deadline = NOW + timedelta(seconds=20)
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^health_probe_after_deadline$"):
        repository.reserve_health_probe(
            running_job.job_id,
            attempt.attempt_id,
            "api-ping-v1",
            "8" * 64,
            deadline,
            deadline + timedelta(microseconds=1),
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


@pytest.mark.parametrize("attempts", [0, -1, True, 1.0, "1"])
def test_health_observation_rejects_invalid_attempt_count_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    attempts: object,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        NOW + timedelta(seconds=20),
        NOW,
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_health_observation$"):
        repository.record_health_observation(
            running_job.job_id,
            attempt.attempt_id,
            "health_probe_healthy",
            attempts=attempts,
            last_failure_code=None,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


@pytest.mark.parametrize(
    ("result_code", "last_failure_code"),
    [
        ("health_probe_reserved", None),
        ("healthy", None),
        ("health_probe_healthy", "unexpected_failure"),
        ("health_probe_unhealthy", None),
        ("health_probe_unknown", None),
        ("health_probe_interrupted", None),
    ],
)
def test_health_observation_rejects_open_or_inconsistent_results_atomically(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    result_code: str,
    last_failure_code: str | None,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        NOW + timedelta(seconds=20),
        NOW,
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_health_observation$"):
        repository.record_health_observation(
            running_job.job_id,
            attempt.attempt_id,
            result_code,
            reservation.attempts,
            last_failure_code,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_health_probe_reservation_is_crash_durable_and_ordinal_cannot_repeat(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        NOW + timedelta(seconds=20),
        NOW,
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^health_probe_already_reserved$"):
        repository.reserve_health_probe(
            running_job.job_id,
            attempt.attempt_id,
            "api-ping-v1",
            "8" * 64,
            NOW + timedelta(seconds=20),
            NOW,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_health_observation_requires_exact_active_attempt_binding(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    _begin_attempt(repository, running_job.job_id, _resolved_material("a"))
    _seed_instance(engine, "instance-2")
    repository.create_job(
        _command("start", "start-2", instance_id="instance-2"),
        "operator_cli",
    )
    other_job = repository.claim_next_job("supervisor-2", lease_seconds=30)
    assert other_job is not None
    other_attempt = _begin_attempt(
        repository,
        other_job.job_id,
        _resolved_material("b"),
        lease_owner="supervisor-2",
        lease_generation=other_job.lease_generation,
    )
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^job_attempt_instance_mismatch$"):
        repository.reserve_health_probe(
            running_job.job_id,
            other_attempt.attempt_id,
            "api-ping-v1",
            "8" * 64,
            NOW + timedelta(seconds=20),
            NOW,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_latest_attempt_rejects_malformed_persisted_health_evidence(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    with Session(engine) as session, session.begin():
        record = session.get(RuntimeAttemptRecord, attempt.attempt_id)
        assert record is not None
        record.health_result = {"result_code": "starting", "attempts": 0}

    with pytest.raises(RuntimeDataError, match=r"^invalid_health_result$"):
        repository.get_latest_attempt_material("instance-1")


@pytest.mark.parametrize(
    "health_result",
    [
        {
            "profile_id": "api-ping-v1",
            "profile_digest": "8" * 64,
            "deadline_at": "2026-07-16T08:00:20Z",
            "next_probe_not_before": "2026-07-16T08:00:00Z",
            "attempts": 1,
            "result_code": "health_probe_healthy",
            "last_failure_code": None,
        },
        {
            "profile_id": "api-ping-v1",
            "profile_digest": "8" * 64,
            "deadline_at": "2026-07-16T08:00:20Z",
            "next_probe_not_before": "2026-07-16T08:00:00Z",
            "observed_at": "2026-07-16T16:00:01+08:00",
            "attempts": 1,
            "result_code": "health_probe_healthy",
            "last_failure_code": None,
        },
        {
            "profile_id": "api-ping-v1",
            "profile_digest": "8" * 64,
            "deadline_at": "2026-07-16T08:00:20Z",
            "next_probe_not_before": "2026-07-16T08:00:00Z",
            "observed_at": "2026-07-16T08:00:01Z",
            "attempts": 1,
            "result_code": "health_probe_healthy",
            "last_failure_code": None,
            "unexpected": "field",
        },
    ],
    ids=["missing-observed-at", "non-utc-observed-at", "extra-field"],
)
def test_latest_attempt_health_evidence_mapping_is_strict(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    health_result: dict[str, object],
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    with Session(engine) as session, session.begin():
        record = session.get(RuntimeAttemptRecord, attempt.attempt_id)
        assert record is not None
        record.health_result = health_result

    with pytest.raises(RuntimeDataError, match=r"^invalid_health_result$"):
        repository.get_latest_attempt_material("instance-1")


def test_latest_attempt_preserves_legacy_nullable_started_at(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    with Session(engine) as session, session.begin():
        record = session.get(RuntimeAttemptRecord, attempt.attempt_id)
        assert record is not None
        record.started_at = None

    recovery = repository.get_latest_attempt_material("instance-1")

    assert recovery is not None
    assert recovery.started_at is None


def test_record_reconciliation_blocked_without_attempt_is_atomic(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    job = repository.record_reconciliation_blocked(
        running_job.job_id,
        None,
        "identity_ambiguous",
        **_lease_args(running_job),
    )

    instance = repository.get_instance("instance-1")
    assert job.status == "needs_reconciliation"
    assert job.failure_code == "identity_ambiguous"
    assert job.lease_owner is None
    assert job.lease_expires_at is None
    assert instance.lifecycle_status == "failed"
    assert instance.failure_latched is True
    assert repository.list_attempts("instance-1") == ()
    with Session(engine) as session:
        audit = session.scalars(
            select(RuntimeAuditEventRecord).order_by(
                RuntimeAuditEventRecord.occurred_at,
                RuntimeAuditEventRecord.audit_event_id,
            )
        ).all()[-1]
    assert audit.result_code == "identity_ambiguous"


def test_record_reconciliation_blocked_cannot_forge_stale_lease(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    before = _database_snapshot(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^reserved_reconciliation_failure_code$",
    ):
        repository.record_reconciliation_blocked(
            running_job.job_id,
            None,
            "stale_lease",
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_record_reconciliation_blocked_preserves_exact_active_attempt(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))
    repository.begin_attempt(
        running_job.job_id,
        attempt_id,
        _resolved_material(),
        **_lease_args(running_job),
    )
    with Session(engine) as session:
        before = tuple(session.execute(select(*RuntimeAttemptRecord.__table__.columns)).one())

    repository.record_reconciliation_blocked(
        running_job.job_id,
        attempt_id,
        "identity_mismatch",
        **_lease_args(running_job),
    )

    with Session(engine) as session:
        after = tuple(session.execute(select(*RuntimeAttemptRecord.__table__.columns)).one())
    assert after == before


def test_record_reconciliation_blocked_rolls_back_if_audit_write_fails(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))
    repository.begin_attempt(
        running_job.job_id,
        attempt_id,
        _resolved_material(),
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected_audit_failure")

    monkeypatch.setattr(repository, "_append_audit_record", fail_audit)
    with pytest.raises(RuntimeError, match=r"^injected_audit_failure$"):
        repository.record_reconciliation_blocked(
            running_job.job_id,
            attempt_id,
            "identity_mismatch",
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


@pytest.mark.parametrize("attempt_id", [None, "attempt-wrong"])
def test_record_reconciliation_blocked_requires_exact_optional_attempt_binding(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    attempt_id: str | None,
) -> None:
    exact_attempt_id = repository.prepare_attempt_id(
        running_job.job_id, **_lease_args(running_job)
    )
    repository.begin_attempt(
        running_job.job_id,
        exact_attempt_id,
        _resolved_material(),
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^active_attempt_binding_mismatch$"):
        repository.record_reconciliation_blocked(
            running_job.job_id,
            attempt_id,
            "identity_mismatch",
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


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
    first = _begin_attempt(repository, running_job.job_id, first_material)
    _record_healthy(repository, running_job, first.attempt_id)

    repository.create_job(_command("stop", "stop-1", version=1), "operator_cli")
    stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert stop_job is not None
    repository.record_stopped(
        stop_job.job_id,
        first.attempt_id,
        exit_code=0,
        **_lease_args(stop_job),
    )

    repository.create_job(_command("start", "start-2", version=2), "operator_cli")
    next_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert next_job is not None
    second = _begin_attempt(repository, next_job.job_id, _resolved_material("b"))

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

    attempt = _begin_attempt(repository, running_job.job_id, material)
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
    assert audit.adapter_template_revision_id == TEMPLATE_REVISION_ID
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
        _begin_attempt(repository, running_job.job_id, material)

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
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))
    clock.now += timedelta(seconds=31)

    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
        repository.begin_attempt(
            running_job.job_id,
            attempt_id,
            _resolved_material(),
            **_lease_args(running_job),
        )

    assert repository.list_attempts("instance-1") == ()


def test_record_healthy_binds_job_and_attempt_and_completes_atomically(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)

    healthy = _record_healthy(repository, running_job, attempt.attempt_id)
    instance, job, record = _persisted_records(engine, running_job.job_id, attempt.attempt_id)

    assert healthy.status == "healthy"
    assert healthy.health_result == "health_probe_healthy"
    assert instance.lifecycle_status == "healthy"
    assert instance.failure_latched is False
    assert job.status == "succeeded"
    assert job.lease_owner is None
    assert job.lease_expires_at is None
    assert record.health_result is not None
    assert record.health_result["result_code"] == "health_probe_healthy"


def test_late_healthy_observation_is_rejected_without_mutation(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    deadline = NOW + timedelta(seconds=20)
    reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        deadline,
        NOW,
        **_lease_args(running_job),
    )
    assert reservation.observed_at == NOW
    clock.now = deadline + timedelta(microseconds=1)
    before = _database_snapshot(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^health_probe_completed_after_deadline$",
    ):
        repository.record_health_observation(
            running_job.job_id,
            attempt.attempt_id,
            "health_probe_healthy",
            reservation.attempts,
            None,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_completed_observation_overwrites_reservation_timestamp_atomically(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        NOW + timedelta(seconds=20),
        NOW,
        **_lease_args(running_job),
    )
    assert reservation.observed_at == NOW
    clock.now = NOW + timedelta(seconds=1)

    repository.record_health_observation(
        running_job.job_id,
        attempt.attempt_id,
        "health_probe_healthy",
        reservation.attempts,
        None,
        **_lease_args(running_job),
    )

    recovery = repository.get_latest_attempt_material("instance-1")
    assert recovery is not None
    assert recovery.health_result is not None
    assert recovery.health_result.observed_at == NOW + timedelta(seconds=1)
    with Session(engine) as session:
        persisted = session.get(RuntimeAttemptRecord, attempt.attempt_id)
        assert persisted is not None
        assert persisted.health_result is not None
        assert persisted.health_result["observed_at"] == "2026-07-16T08:00:01Z"


def test_persisted_late_healthy_evidence_cannot_be_adopted_after_restart(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    deadline = NOW + timedelta(seconds=20)
    reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        deadline,
        NOW,
        **_lease_args(running_job),
    )
    late_observation = deadline + timedelta(microseconds=1)
    with Session(engine) as session, session.begin():
        record = session.get(RuntimeAttemptRecord, attempt.attempt_id)
        assert record is not None
        record.health_result = {
            **reservation.model_dump(mode="json"),
            "observed_at": late_observation.isoformat().replace("+00:00", "Z"),
            "result_code": "health_probe_healthy",
        }
    clock.now = late_observation

    restarted = SqlRuntimeRepository(engine, clock=clock, id_factory=SequentialIds())
    recovery = restarted.get_latest_attempt_material("instance-1")
    assert recovery is not None
    assert recovery.health_result is not None
    assert recovery.health_result.result_code == "health_probe_healthy"
    assert recovery.health_result.observed_at == late_observation
    assert recovery.health_result.observed_at > recovery.health_result.deadline_at
    before = _database_snapshot(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^healthy_probe_evidence_expired$",
    ):
        restarted.record_healthy(
            running_job.job_id,
            attempt.attempt_id,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


@pytest.mark.parametrize(
    "evidence_result",
    [
        None,
        "health_probe_reserved",
        "health_probe_unhealthy",
        "health_probe_unknown",
        "health_probe_interrupted",
    ],
)
def test_record_healthy_requires_exact_completed_healthy_probe_evidence(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    evidence_result: str | None,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    if evidence_result is not None:
        reservation = repository.reserve_health_probe(
            running_job.job_id,
            attempt.attempt_id,
            "api-ping-v1",
            "8" * 64,
            NOW + timedelta(seconds=20),
            NOW,
            **_lease_args(running_job),
        )
        if evidence_result != "health_probe_reserved":
            repository.record_health_observation(
                running_job.job_id,
                attempt.attempt_id,
                evidence_result,
                reservation.attempts,
                evidence_result,
                **_lease_args(running_job),
            )
    before = _database_snapshot(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^healthy_probe_evidence_required$",
    ):
        repository.record_healthy(
            running_job.job_id,
            attempt.attempt_id,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_transition_rejects_attempt_from_another_instance_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    first = _begin_attempt(repository, running_job.job_id, _resolved_material("a"))
    _seed_instance(engine, "instance-2")
    repository.create_job(
        _command("start", "start-2", instance_id="instance-2"),
        "operator_cli",
    )
    other_job = repository.claim_next_job("supervisor-2", lease_seconds=30)
    assert other_job is not None
    other = _begin_attempt(
        repository,
        other_job.job_id,
        _resolved_material("b"),
        lease_owner="supervisor-2",
        lease_generation=other_job.lease_generation,
    )

    with pytest.raises(RuntimeInvalidTransition, match=r"^job_attempt_instance_mismatch$"):
        repository.record_healthy(
            running_job.job_id,
            other.attempt_id,
            **_lease_args(running_job),
        )

    assert repository.list_attempts("instance-1")[0] == first
    assert repository.list_attempts("instance-2")[0] == other
    assert repository.list_jobs("instance-1")[0].status == "running"


def test_failed_attempt_latches_without_queuing_retry(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)

    failed = repository.record_failed(
        running_job.job_id,
        attempt.attempt_id,
        "health_timeout",
        **_lease_args(running_job),
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


def test_record_failed_preserves_durable_health_observation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        NOW + timedelta(seconds=20),
        NOW,
        **_lease_args(running_job),
    )
    repository.record_health_observation(
        running_job.job_id,
        attempt.attempt_id,
        "health_probe_unhealthy",
        attempts=reservation.attempts,
        last_failure_code="health_timeout",
        **_lease_args(running_job),
    )

    repository.record_failed(
        running_job.job_id,
        attempt.attempt_id,
        "health_timeout",
        **_lease_args(running_job),
    )

    _, job, record = _persisted_records(engine, running_job.job_id, attempt.attempt_id)
    assert job.status == "failed"
    assert record.health_result == {
        "profile_id": "api-ping-v1",
        "profile_digest": "8" * 64,
        "deadline_at": (NOW + timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
        "next_probe_not_before": NOW.isoformat().replace("+00:00", "Z"),
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "result_code": "health_probe_unhealthy",
        "attempts": 1,
        "last_failure_code": "health_timeout",
    }
    assert all(item.status != "pending" for item in repository.list_jobs("instance-1"))


def test_reclaim_stale_reconciliation_job_restores_bounded_running_lease(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    clock.now += timedelta(seconds=31)
    stale = repository.claim_next_job("supervisor-reaper", lease_seconds=30)
    assert stale is not None
    assert stale.status == "needs_reconciliation"
    reclaim_time = clock.now

    reclaimed = repository.reclaim_reconciliation_job(
        running_job.job_id,
        "supervisor-2",
        lease_seconds=45,
    )

    assert reclaimed.status == "running"
    assert reclaimed.lease_owner == "supervisor-2"
    assert reclaimed.lease_generation == running_job.lease_generation + 1
    assert reclaimed.lease_expires_at == reclaim_time + timedelta(seconds=45)
    assert reclaimed.completed_at is None
    assert reclaimed.failure_code is None
    assert repository.list_attempts("instance-1")[0] == attempt
    assert all(item.status != "pending" for item in repository.list_jobs("instance-1"))
    with Session(engine) as session:
        audit = session.scalars(
            select(RuntimeAuditEventRecord).where(
                RuntimeAuditEventRecord.request_id == running_job.job_id
            ).order_by(
                RuntimeAuditEventRecord.occurred_at,
                RuntimeAuditEventRecord.audit_event_id,
            )
        ).all()[-1]
    assert audit.actor_type == "supervisor-2"
    assert audit.result_code == "reconciliation_reclaimed"
    assert repository.assert_current_lease(
        running_job.job_id,
        "supervisor-2",
        reclaimed.lease_generation,
    ) == reclaimed


def test_reclaimed_lease_generation_fences_the_previous_worker_atomically(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    clock.now += timedelta(seconds=31)
    assert repository.claim_next_job("supervisor-reaper", lease_seconds=30) is not None
    reclaimed = repository.reclaim_reconciliation_job(
        running_job.job_id,
        "supervisor-1",
        lease_seconds=30,
    )
    assert reclaimed.lease_generation == running_job.lease_generation + 1
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_generation_mismatch$"):
        repository.record_failed(
            running_job.job_id,
            attempt.attempt_id,
            "stale_worker_write",
            lease_owner="supervisor-1",
            lease_generation=running_job.lease_generation,
        )

    assert _database_snapshot(engine) == before


def test_health_observation_rollback_restores_reserved_ordinal(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    reservation = repository.reserve_health_probe(
        running_job.job_id,
        attempt.attempt_id,
        "api-ping-v1",
        "8" * 64,
        NOW + timedelta(seconds=20),
        NOW,
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    def fail_view(_record: RuntimeAttemptRecord) -> RuntimeAttemptView:
        raise RuntimeError("injected_view_failure")

    monkeypatch.setattr(repository, "_attempt_view", fail_view)
    with pytest.raises(RuntimeError, match=r"^injected_view_failure$"):
        repository.record_health_observation(
            running_job.job_id,
            attempt.attempt_id,
            "health_probe_healthy",
            reservation.attempts,
            None,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


@pytest.mark.parametrize("lease_seconds", [0, 3601, True, 1.0, "1"])
def test_reclaim_reconciliation_job_rejects_invalid_lease_without_mutation(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
    lease_seconds: object,
) -> None:
    clock.now += timedelta(seconds=31)
    assert repository.claim_next_job("supervisor-reaper", lease_seconds=30) is not None
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_lease_seconds$"):
        repository.reclaim_reconciliation_job(
            running_job.job_id,
            "supervisor-2",
            lease_seconds=lease_seconds,
        )

    assert _database_snapshot(engine) == before


def test_reclaim_reconciliation_job_rejects_non_stale_reconciliation_and_terminals(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    repository.record_reconciliation_blocked(
        running_job.job_id,
        None,
        "identity_ambiguous",
        **_lease_args(running_job),
    )
    before = _database_snapshot(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^stale_lease_reconciliation_required$",
    ):
        repository.reclaim_reconciliation_job(
            running_job.job_id,
            "supervisor-2",
            lease_seconds=30,
        )
    assert _database_snapshot(engine) == before

    with Session(engine) as session, session.begin():
        job = session.get(RuntimeLifecycleJobRecord, running_job.job_id)
        assert job is not None
        job.status = "failed"
        job.failure_code = "stale_lease"
    terminal_before = _database_snapshot(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^stale_lease_reconciliation_required$",
    ):
        repository.reclaim_reconciliation_job(
            running_job.job_id,
            "supervisor-2",
            lease_seconds=30,
        )
    assert _database_snapshot(engine) == terminal_before


def test_reclaim_reconciliation_job_rolls_back_when_audit_fails(
    engine: Engine,
    clock: MutableClock,
    monkeypatch: pytest.MonkeyPatch,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    clock.now += timedelta(seconds=31)
    assert repository.claim_next_job("supervisor-reaper", lease_seconds=30) is not None
    before = _database_snapshot(engine)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected_audit_failure")

    monkeypatch.setattr(repository, "_append_audit_record", fail_audit)
    with pytest.raises(RuntimeError, match=r"^injected_audit_failure$"):
        repository.reclaim_reconciliation_job(
            running_job.job_id,
            "supervisor-2",
            lease_seconds=30,
        )

    assert _database_snapshot(engine) == before


def test_record_stopped_requires_explicit_stop_job_and_transitions_all_records(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    _record_healthy(repository, running_job, attempt.attempt_id)
    repository.create_job(_command("stop", "stop-1", version=1), "operator_cli")
    stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert stop_job is not None

    stopped = repository.record_stopped(
        stop_job.job_id,
        attempt.attempt_id,
        exit_code=0,
        **_lease_args(stop_job),
    )
    instance, job, record = _persisted_records(engine, stop_job.job_id, attempt.attempt_id)

    assert stopped.status == "stopped"
    assert stopped.exit_code == 0
    assert instance.desired_state == "stopped"
    assert instance.lifecycle_status == "stopped"
    assert job.status == "succeeded"
    assert record.stopped_at == NOW.replace(tzinfo=None)


def test_record_stopped_accepts_already_absent_without_inventing_exit_code(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))
    attempt = repository.begin_attempt(
        running_job.job_id,
        attempt_id,
        _resolved_material(),
        **_lease_args(running_job),
    )
    _record_healthy(repository, running_job, attempt.attempt_id)
    repository.create_job(_command("stop", "stop-absent", version=1), "operator_cli")
    stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert stop_job is not None

    stopped = repository.record_stopped(
        stop_job.job_id,
        attempt.attempt_id,
        exit_code=None,
        **_lease_args(stop_job),
    )

    assert stopped.status == "stopped"
    assert stopped.exit_code is None
    with Session(engine) as session:
        audit = session.scalar(
            select(RuntimeAuditEventRecord).where(
                RuntimeAuditEventRecord.request_id == stop_job.job_id,
                RuntimeAuditEventRecord.result_code == "container_already_absent",
            )
        )
    assert audit is not None
    assert audit.result_code == "container_already_absent"


@pytest.mark.parametrize("exit_code", [True, "0", 0.0])
def test_record_stopped_rejects_non_integer_exit_codes(
    repository: SqlRuntimeRepository,
    exit_code: object,
) -> None:
    with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_exit_code$"):
        repository.record_stopped(
            "job-1",
            "attempt-1",
            exit_code=exit_code,
            lease_owner="supervisor-1",
            lease_generation=1,
        )


def test_latch_failure_handles_current_job_without_creating_or_changing_attempt(
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    failed_job = repository.latch_failure(
        running_job.job_id,
        "container_missing",
        **_lease_args(running_job),
    )

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
    renewed = repository.renew_lease(
        running_job.job_id,
        "supervisor-1",
        running_job.lease_generation,
        lease_seconds=60,
    )

    assert renewed.lease_owner == "supervisor-1"
    assert renewed.lease_generation == running_job.lease_generation
    assert renewed.lease_expires_at == NOW + timedelta(seconds=60)
    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_owner_mismatch$"):
        repository.renew_lease(
            running_job.job_id,
            "supervisor-2",
            running_job.lease_generation,
            lease_seconds=60,
        )
    for invalid_seconds in (0, 3601, True):
        with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_lease_seconds$"):
            repository.renew_lease(
                running_job.job_id,
                "supervisor-1",
                running_job.lease_generation,
                lease_seconds=invalid_seconds,
            )
    clock.now += timedelta(seconds=61)
    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
        repository.renew_lease(
            running_job.job_id,
            "supervisor-1",
            running_job.lease_generation,
            lease_seconds=60,
        )


def test_renew_lease_preserves_running_attempt_and_generation(
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job: RuntimeJobView,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    clock.now += timedelta(seconds=5)

    renewed = repository.renew_lease(
        running_job.job_id,
        "supervisor-1",
        running_job.lease_generation,
        lease_seconds=60,
    )

    assert renewed.status == "running"
    assert renewed.lease_owner == running_job.lease_owner
    assert renewed.lease_generation == running_job.lease_generation
    assert renewed.lease_expires_at == clock.now + timedelta(seconds=60)
    assert repository.list_attempts("instance-1") == (attempt,)


def test_audit_failure_rolls_back_healthy_attempt_job_and_instance(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt = _begin_attempt(repository, running_job.job_id)
    _complete_health_probe(repository, running_job, attempt.attempt_id)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected_audit_failure")

    monkeypatch.setattr(repository, "_append_audit_record", fail_audit)
    with pytest.raises(RuntimeError, match=r"^injected_audit_failure$"):
        repository.record_healthy(
            running_job.job_id,
            attempt.attempt_id,
            **_lease_args(running_job),
        )

    instance, job, record = _persisted_records(engine, running_job.job_id, attempt.attempt_id)
    assert instance.lifecycle_status == "starting"
    assert job.status == "running"
    assert job.lease_owner == "supervisor-1"
    assert record.status == "launching"
    assert record.health_result is not None
    assert record.health_result["result_code"] == "health_probe_healthy"


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
        _begin_attempt(repository, running_job.job_id, material)

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
        _begin_attempt(
            repository,
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
    attempt = _begin_attempt(repository, running_job.job_id)
    job_id = running_job.job_id
    lease_job = running_job
    if attempt_status == "healthy":
        _record_healthy(repository, running_job, attempt.attempt_id)
        repository.create_job(_command("stop", "stop-active", version=1), "operator_cli")
        stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
        assert stop_job is not None
        job_id = stop_job.job_id
        lease_job = stop_job

    before = _database_snapshot(engine)
    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^active_attempt_requires_explicit_failure$",
    ):
        repository.latch_failure(
            job_id,
            "container_missing",
            **_lease_args(lease_job),
        )

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
    lease_job = running_job
    attempt_id: str | None = None
    prepared_attempt_id: str | None = None
    if operation == "begin_attempt":
        prepared_attempt_id = repository.prepare_attempt_id(job_id, **_lease_args(lease_job))
    if operation in {"record_healthy", "record_failed", "record_stopped"}:
        attempt = _begin_attempt(repository, running_job.job_id)
        attempt_id = attempt.attempt_id
        _complete_health_probe(repository, running_job, attempt.attempt_id)
        if operation == "record_stopped":
            repository.record_healthy(
                running_job.job_id,
                attempt.attempt_id,
                **_lease_args(running_job),
            )
            repository.create_job(_command("stop", "stop-expiry", version=1), "operator_cli")
            stop_job = repository.claim_next_job("supervisor-1", lease_seconds=30)
            assert stop_job is not None
            job_id = stop_job.job_id
            lease_job = stop_job

    def invoke() -> object:
        if operation == "begin_attempt":
            return repository.begin_attempt(
                job_id,
                prepared_attempt_id,
                _resolved_material(),
                **_lease_args(lease_job),
            )
        if operation == "record_healthy":
            return repository.record_healthy(job_id, attempt_id, **_lease_args(lease_job))
        if operation == "record_failed":
            return repository.record_failed(
                job_id,
                attempt_id,
                "health_timeout",
                **_lease_args(lease_job),
            )
        if operation == "record_stopped":
            return repository.record_stopped(
                job_id,
                attempt_id,
                exit_code=0,
                **_lease_args(lease_job),
            )
        if operation == "renew_lease":
            return repository.renew_lease(
                job_id,
                "supervisor-1",
                lease_job.lease_generation,
                lease_seconds=30,
            )
        return repository.latch_failure(
            job_id,
            "container_missing",
            **_lease_args(lease_job),
        )

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


@pytest.mark.parametrize("operation", ["prepare_attempt_id", "record_reconciliation_blocked"])
def test_new_supervisor_transitions_validate_lease_with_post_lock_time(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
    operation: str,
) -> None:
    def invoke() -> object:
        if operation == "prepare_attempt_id":
            return repository.prepare_attempt_id(
                running_job.job_id, **_lease_args(running_job)
            )
        return repository.record_reconciliation_blocked(
            running_job.job_id,
            None,
            "identity_ambiguous",
            **_lease_args(running_job),
        )

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
    _begin_attempt(repository, running_job.job_id)
    before = _database_snapshot(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^attempt_transition_required$"):
        repository.complete_job(
            running_job.job_id,
            status,
            failure_code,
            **_lease_args(running_job),
        )

    assert _database_snapshot(engine) == before


def test_complete_job_samples_expiry_after_the_job_lock(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    clock.set_sequence(NOW, NOW + timedelta(seconds=31))
    lock_query_seen = False

    def consume_time_while_waiting_for_lock(
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

    event.listen(engine, "before_cursor_execute", consume_time_while_waiting_for_lock)
    try:
        completed = repository.complete_job(
            running_job.job_id,
            "succeeded",
            None,
            **_lease_args(running_job),
        )
    finally:
        event.remove(engine, "before_cursor_execute", consume_time_while_waiting_for_lock)

    assert lock_query_seen is True
    assert completed.status == "needs_reconciliation"
    assert completed.failure_code == "stale_lease"


def test_begin_attempt_locks_exact_provenance_rows_in_fixed_order(
    monkeypatch: pytest.MonkeyPatch,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    statements: list[str] = []
    original_scalar = Session.scalar
    original_scalars = Session.scalars

    def compile_statement(statement: object) -> None:
        if not hasattr(statement, "compile"):
            return
        statements.append(
            str(statement.compile(dialect=postgresql.dialect())).upper()
        )

    def record_scalar(
        session: Session,
        statement: object,
        *args: object,
        **kwargs: object,
    ):
        compile_statement(statement)
        return original_scalar(session, statement, *args, **kwargs)

    def record_scalars(
        session: Session,
        statement: object,
        *args: object,
        **kwargs: object,
    ):
        compile_statement(statement)
        return original_scalars(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", record_scalar)
    monkeypatch.setattr(Session, "scalars", record_scalars)

    _begin_attempt(repository, running_job.job_id)

    tables = (
        "RUNTIME_LIFECYCLE_JOBS",
        "RUNTIME_INSTANCES",
        "RUNTIME_ATTEMPTS",
        "ADAPTER_TEMPLATE_REVISIONS",
        "SECRET_REFERENCES",
        "SECRET_VERSION_METADATA",
    )
    locked_queries = [
        next(
            statement
            for statement in statements
            if f"FROM {table}" in statement and "FOR UPDATE" in statement
        )
        for table in tables
    ]
    indices = [statements.index(statement) for statement in locked_queries]
    assert indices == sorted(indices)
    assert "ORDER BY SECRET_REFERENCES.SECRET_REFERENCE_ID" in locked_queries[4]
    assert (
        "ORDER BY SECRET_VERSION_METADATA.SECRET_REFERENCE_ID, "
        "SECRET_VERSION_METADATA.VERSION_ID"
    ) in locked_queries[5]


def test_begin_attempt_validates_lease_after_secret_version_lock(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))
    before = _database_snapshot(engine)
    clock.set_sequence(NOW, NOW + timedelta(seconds=31))
    version_query_seen = False

    def consume_time_after_version_query(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal version_query_seen
        if not version_query_seen and "FROM secret_version_metadata" in statement:
            version_query_seen = True
            clock()

    event.listen(engine, "after_cursor_execute", consume_time_after_version_query)
    try:
        with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
            repository.begin_attempt(
                running_job.job_id,
                attempt_id,
                _resolved_material(),
                **_lease_args(running_job),
            )
    finally:
        event.remove(engine, "after_cursor_execute", consume_time_after_version_query)

    assert version_query_seen is True
    assert _database_snapshot(engine) == before


@pytest.mark.parametrize("operation", ["renew_lease", "latch_failure"])
def test_pre_attempt_supervisor_audit_uses_runtime_spec_template_binding(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    operation: str,
) -> None:
    if operation == "renew_lease":
        repository.renew_lease(
            running_job.job_id,
            "supervisor-1",
            running_job.lease_generation,
            lease_seconds=60,
        )
    else:
        repository.latch_failure(
            running_job.job_id,
            "container_missing",
            **_lease_args(running_job),
        )

    with Session(engine) as session:
        audit = session.scalars(
            select(RuntimeAuditEventRecord).order_by(
                RuntimeAuditEventRecord.occurred_at,
                RuntimeAuditEventRecord.audit_event_id,
            )
        ).all()[-1]
    assert audit.adapter_template_revision_id == TEMPLATE_REVISION_ID


def _postgres_running_repository(
    engine: Engine,
) -> tuple[SqlRuntimeRepository, RuntimeJobView]:
    _seed_instance(engine)
    repository = SqlRuntimeRepository(engine, clock=MutableClock(), id_factory=SequentialIds())
    repository.create_job(_command("start", "start-1"), "operator_cli")
    job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert job is not None
    return repository, job


def test_seeded_template_revision_supports_formal_revoke_writer(engine: Engine) -> None:
    _seed_instance(engine)

    revoked = SqlTemplateRepository(engine).revoke_template(
        TEMPLATE_REVISION_ID,
        "platform-admin",
        NOW,
    )

    assert revoked.revision_id == TEMPLATE_REVISION_ID
    assert revoked.status.value == "revoked"


def test_postgres_begin_waits_for_retired_version_then_rejects_without_transition_mutation(
    postgres_engine: Engine,
) -> None:
    repository, running_job = _postgres_running_repository(postgres_engine)
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with Session(postgres_engine) as writer, writer.begin():
            version = writer.scalar(
                select(SecretVersionMetadataRecord)
                .where(
                    SecretVersionMetadataRecord.secret_reference_id == "exchange",
                    SecretVersionMetadataRecord.version_id == "secret-version-1",
                )
                .with_for_update()
            )
            assert version is not None
            version.status = "retired"
            version.retired_at = NOW
            writer.flush()
            future = executor.submit(
                repository.begin_attempt,
                running_job.job_id,
                attempt_id,
                _resolved_material(),
                running_job.lease_owner,
                running_job.lease_generation,
            )
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)

        with pytest.raises(RuntimeInvalidTransition, match=r"^secret_version_inactive$"):
            future.result(timeout=5)
    finally:
        executor.shutdown(wait=True)

    assert repository.list_attempts("instance-1") == ()
    instance = repository.get_instance("instance-1")
    assert instance.lifecycle_status == "registered"
    with Session(postgres_engine) as session:
        job = session.get(RuntimeLifecycleJobRecord, running_job.job_id)
        assert job is not None
        assert job.status == "claimed"
        assert session.scalar(
            select(RuntimeAuditEventRecord).where(
                RuntimeAuditEventRecord.result_code == "attempt_started"
            )
        ) is None


def test_postgres_begin_linearizes_before_template_revoke_writer(
    postgres_engine: Engine,
) -> None:
    repository, running_job = _postgres_running_repository(postgres_engine)
    attempt_id = repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job))
    provenance_locked = Event()
    release_begin = Event()

    def pause_after_version_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "FROM secret_version_metadata" not in statement or "FOR UPDATE" not in statement:
            return
        provenance_locked.set()
        if not release_begin.wait(timeout=5):
            raise RuntimeError("timed_out_releasing_begin_attempt")

    event.listen(postgres_engine, "after_cursor_execute", pause_after_version_lock)
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        begin_future = executor.submit(
            repository.begin_attempt,
            running_job.job_id,
            attempt_id,
            _resolved_material(),
            running_job.lease_owner,
            running_job.lease_generation,
        )
        assert provenance_locked.wait(timeout=5)
        revoke_future = executor.submit(
            SqlTemplateRepository(postgres_engine).revoke_template,
            TEMPLATE_REVISION_ID,
            "platform-admin",
            NOW,
        )
        with pytest.raises(FutureTimeoutError):
            revoke_future.result(timeout=0.2)

        release_begin.set()
        attempt = begin_future.result(timeout=5)
        revoked = revoke_future.result(timeout=5)
    finally:
        release_begin.set()
        event.remove(postgres_engine, "after_cursor_execute", pause_after_version_lock)
        executor.shutdown(wait=True)

    assert attempt.status == "launching"
    assert revoked.status.value == "revoked"


def test_postgres_two_prepared_candidates_create_at_most_one_active_attempt(
    postgres_engine: Engine,
) -> None:
    repository, running_job = _postgres_running_repository(postgres_engine)
    attempt_ids = (
        repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job)),
        repository.prepare_attempt_id(running_job.job_id, **_lease_args(running_job)),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(
                repository.begin_attempt,
                running_job.job_id,
                attempt_id,
                _resolved_material(),
                running_job.lease_owner,
                running_job.lease_generation,
            )
            for attempt_id in attempt_ids
        )

    attempts = []
    failures = []
    for future in futures:
        try:
            attempts.append(future.result())
        except RuntimeInvalidTransition as error:
            failures.append(str(error))

    assert len(attempts) == 1
    assert failures == ["active_attempt_exists"]
    assert {attempt.attempt_id for attempt in repository.list_attempts("instance-1")} == {
        attempts[0].attempt_id
    }


def test_postgres_concurrent_reclaims_allow_one_owner_and_fence_previous_generation(
    postgres_engine: Engine,
    clock: MutableClock,
) -> None:
    _seed_instance(postgres_engine)
    repository = SqlRuntimeRepository(postgres_engine, clock=clock)
    repository.create_job(_command("start", "start-1"), "operator_cli")
    claimed = repository.claim_next_job("supervisor-original", lease_seconds=30)
    assert claimed is not None
    attempt = _begin_attempt(
        repository,
        claimed.job_id,
        lease_owner="supervisor-original",
        lease_generation=claimed.lease_generation,
    )
    clock.now += timedelta(seconds=31)
    stale = repository.claim_next_job("supervisor-reaper", lease_seconds=30)
    assert stale is not None
    assert stale.status == "needs_reconciliation"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            owner: executor.submit(
                repository.reclaim_reconciliation_job,
                claimed.job_id,
                owner,
                30,
            )
            for owner in ("supervisor-a", "supervisor-b")
        }

    successes = []
    failures = []
    for owner, future in futures.items():
        try:
            successes.append((owner, future.result()))
        except RuntimeInvalidTransition as error:
            failures.append(str(error))

    assert len(successes) == 1
    assert failures == ["stale_lease_reconciliation_required"]
    winning_owner, reclaimed = successes[0]
    assert reclaimed.lease_owner == winning_owner
    assert reclaimed.lease_generation == claimed.lease_generation + 1

    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_generation_mismatch$"):
        repository.record_failed(
            claimed.job_id,
            attempt.attempt_id,
            "stale_worker_write",
            lease_owner="supervisor-original",
            lease_generation=claimed.lease_generation,
        )


def test_postgres_claim_prioritizes_preexisting_stale_before_new_expiry_and_pending(
    postgres_engine: Engine,
    clock: MutableClock,
) -> None:
    for instance_id in ("instance-1", "instance-2", "instance-3"):
        _seed_instance(postgres_engine, instance_id)
    repository = SqlRuntimeRepository(
        postgres_engine,
        clock=clock,
        id_factory=SequentialIds(),
    )
    repository.create_job(
        _command("start", "start-1", instance_id="instance-1"),
        "operator_cli",
    )
    first = repository.claim_next_job("supervisor-original", lease_seconds=30)
    assert first is not None
    repository.create_job(
        _command("start", "start-2", instance_id="instance-2"),
        "operator_cli",
    )
    second = repository.claim_next_job("supervisor-original", lease_seconds=30)
    assert second is not None
    repository.create_job(
        _command("start", "start-3", instance_id="instance-3"),
        "operator_cli",
    )

    clock.now += timedelta(seconds=31)
    discovered = repository.claim_next_job("supervisor-reaper", lease_seconds=30)
    assert discovered is not None
    assert discovered.job_id == first.job_id
    assert discovered.status == "needs_reconciliation"
    before_rediscovery = discovered

    rediscovered = repository.claim_next_job("supervisor-restarted", lease_seconds=30)
    assert rediscovered is not None
    assert rediscovered == before_rediscovery
    assert rediscovered.lease_owner is None
    assert rediscovered.lease_generation == first.lease_generation
    assert rediscovered.failure_code == "stale_lease"

    repository.reclaim_reconciliation_job(
        first.job_id,
        "supervisor-restarted",
        30,
    )
    next_stale = repository.claim_next_job("supervisor-reaper", lease_seconds=30)
    assert next_stale is not None
    assert next_stale.job_id == second.job_id
    assert next_stale.status == "needs_reconciliation"
    repository.reclaim_reconciliation_job(
        second.job_id,
        "supervisor-reaper",
        30,
    )
    pending = repository.claim_next_job("supervisor-final", lease_seconds=30)
    assert pending is not None
    assert pending.instance_id == "instance-3"
    assert pending.status == "claimed"
