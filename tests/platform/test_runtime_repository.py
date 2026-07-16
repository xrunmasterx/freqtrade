from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session

import freqtrade.platform as platform
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_domain import RuntimeAction, RuntimeLifecycleCommand
from freqtrade.platform.runtime_models import (
    RuntimeAttemptRecord,
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
    RuntimeLifecycleJobRecord,
)
from freqtrade.platform.runtime_repository import (
    CompletionStatus,
    RuntimeAuditEvent,
    RuntimeConflict,
    RuntimeDataError,
    RuntimeInstanceAuditState,
    RuntimeInvalidTransition,
    RuntimeNotFound,
    RuntimeQueryRepository,
    RuntimeRepository,
    SqlRuntimeRepository,
)
from freqtrade.platform.template_models import (
    AdapterTemplateRevisionRecord,
    RuntimeSpecRevisionRecord,
    StateAllocationRecord,
)


BACKEND_ROOT = Path(__file__).parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic-platform.ini"
NOW = datetime(2026, 7, 12, 8, tzinfo=UTC)
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
    return SqlRuntimeRepository(engine, clock=clock, id_factory=SequentialIds())


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
def postgres_repository(
    postgres_engine: Engine,
    clock: MutableClock,
) -> SqlRuntimeRepository:
    return SqlRuntimeRepository(postgres_engine, clock=clock, id_factory=SequentialIds())


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


def _seed_instance(engine: Engine, instance_id: str = "instance-1", **updates: object) -> None:
    values: dict[str, object] = {
        "instance_id": instance_id,
        "instance_kind": "execution_worker",
        "owner_kind": "paper_probe",
        "owner_id": "owner-1",
        "owner_revision": "owner-revision-1",
        "management_mode": "supervisor",
        "runtime_spec_revision_id": RUNTIME_SPEC_REVISION_ID,
        "environment": "paper",
        "state_allocation_id": "state-allocation-1",
        "desired_state": "stopped",
        "lifecycle_status": "registered",
        "failure_latched": False,
        "optimistic_version": 0,
        "created_at": NOW,
        "retired_at": None,
    }
    values.update(updates)
    with Session(engine) as session, session.begin():
        _seed_runtime_parent_chain(session)
        session.add(RuntimeInstanceRecord(**values))


def _seed_attempt(
    engine: Engine,
    instance_id: str = "instance-1",
    attempt_id: str = "attempt-1",
    **updates: object,
) -> None:
    values: dict[str, object] = {
        "attempt_id": attempt_id,
        "instance_id": instance_id,
        "attempt_number": 1,
        "runtime_spec_revision_id": RUNTIME_SPEC_REVISION_ID,
        "adapter_template_revision_id": "adapter-template-1",
        "resolved_secret_versions": {"exchange": "version-1"},
        "image_id": "sha256:image-1",
        "root_commit": "1" * 40,
        "backend_commit": "2" * 40,
        "frontend_commit": "3" * 40,
        "strategies_commit": "4" * 40,
        "project_identity": "project-1",
        "container_identity": "container-1",
        "status": "healthy",
        "health_result": None,
        "started_at": NOW,
        "stopped_at": None,
        "exit_code": None,
        "failure_code": None,
    }
    values.update(updates)
    with Session(engine) as session, session.begin():
        session.add(RuntimeAttemptRecord(**values))


def _command(
    action: RuntimeAction | str,
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


def _counts(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(RuntimeInstanceRecord)) or 0,
            session.scalar(select(func.count()).select_from(RuntimeLifecycleJobRecord)) or 0,
            session.scalar(select(func.count()).select_from(RuntimeAuditEventRecord)) or 0,
        )


def test_repository_contracts_are_public_protocols(repository: SqlRuntimeRepository) -> None:
    assert isinstance(repository, RuntimeQueryRepository)
    assert isinstance(repository, RuntimeRepository)
    for public_name in (
        "RuntimeAuditEvent",
        "RuntimeConflict",
        "RuntimeDataError",
        "RuntimeInstanceAuditState",
        "RuntimeInvalidTransition",
        "RuntimeNotFound",
        "RuntimeQueryRepository",
        "RuntimeRepository",
        "SqlRuntimeRepository",
    ):
        assert public_name in platform.__all__


def test_create_job_checks_idempotency_before_version_and_audits_once(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine)

    first = repository.create_job(_command("start", "key-1"), "operator_cli")
    replay = repository.create_job(_command("start", "key-1"), "operator_cli")

    assert replay == first
    instance = repository.get_instance("instance-1")
    assert instance.desired_state == "running"
    assert instance.optimistic_version == 1
    assert len(repository.list_jobs("instance-1")) == 1
    with Session(engine) as session:
        audits = session.scalars(select(RuntimeAuditEventRecord)).all()
    assert len(audits) == 1
    assert set(audits[0].previous_state) == {
        "desired_state",
        "lifecycle_status",
        "failure_latched",
        "optimistic_version",
    }
    assert set(audits[0].next_state) == set(audits[0].previous_state)
    assert audits[0].provenance == {"source": "runtime_repository"}


def test_postgres_identical_idempotency_replay_persists_only_original_mutation(
    postgres_repository: SqlRuntimeRepository,
    postgres_engine: Engine,
) -> None:
    _seed_instance(postgres_engine)

    first = postgres_repository.create_job(_command("start", "key-1"), "operator_cli")
    replay = postgres_repository.create_job(_command("start", "key-1"), "operator_cli")

    assert replay == first
    with Session(postgres_engine) as session:
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        jobs = session.scalars(select(RuntimeLifecycleJobRecord)).all()
        audits = session.scalars(select(RuntimeAuditEventRecord)).all()
        assert instance is not None
        assert instance.desired_state == "running"
        assert instance.optimistic_version == 1
        assert len(jobs) == 1
        assert jobs[0].job_id == first.job_id
        assert len(audits) == 1


@pytest.mark.parametrize(
    ("action", "version"),
    [("stop", 0), ("start", 1)],
    ids=["different-action", "different-expected-version"],
)
def test_create_job_rejects_conflicting_idempotency_payload(
    repository: SqlRuntimeRepository,
    engine: Engine,
    action: str,
    version: int,
) -> None:
    _seed_instance(engine)
    repository.create_job(_command("start", "key-1"), "operator_cli")

    with pytest.raises(RuntimeConflict, match=r"^idempotency_key_conflict$"):
        repository.create_job(_command(action, "key-1", version=version), "operator_cli")

    assert len(repository.list_jobs("instance-1")) == 1
    assert _counts(engine) == (1, 1, 1)


@pytest.mark.parametrize(
    ("action", "version"),
    [("stop", 0), ("start", 1)],
    ids=["different-action", "different-expected-version"],
)
def test_postgres_conflicting_idempotency_payload_preserves_original_state(
    postgres_repository: SqlRuntimeRepository,
    postgres_engine: Engine,
    action: str,
    version: int,
) -> None:
    _seed_instance(postgres_engine)
    original = postgres_repository.create_job(_command("start", "key-1"), "operator_cli")

    with pytest.raises(RuntimeConflict, match=r"^idempotency_key_conflict$"):
        postgres_repository.create_job(_command(action, "key-1", version=version), "operator_cli")

    with Session(postgres_engine) as session:
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        jobs = session.scalars(select(RuntimeLifecycleJobRecord)).all()
        audits = session.scalars(select(RuntimeAuditEventRecord)).all()
        assert instance is not None
        assert instance.desired_state == "running"
        assert instance.optimistic_version == 1
        assert len(jobs) == 1
        assert jobs[0].job_id == original.job_id
        assert jobs[0].requested_action == "start"
        assert jobs[0].expected_instance_version == 0
        assert len(audits) == 1


def test_stale_version_rolls_back_everything(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine)

    with pytest.raises(RuntimeConflict, match=r"^stale_instance_version$"):
        repository.create_job(_command("start", "key-1", version=9), "operator_cli")

    assert repository.get_instance("instance-1").optimistic_version == 0
    assert repository.list_jobs("instance-1") == ()
    assert _counts(engine) == (1, 0, 0)


def test_postgres_stale_version_rolls_back_every_persisted_effect(
    postgres_repository: SqlRuntimeRepository,
    postgres_engine: Engine,
) -> None:
    _seed_instance(postgres_engine)

    with pytest.raises(RuntimeConflict, match=r"^stale_instance_version$"):
        postgres_repository.create_job(_command("start", "key-1", version=9), "operator_cli")

    with Session(postgres_engine) as session:
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        assert instance is not None
        assert instance.desired_state == "stopped"
        assert instance.optimistic_version == 0
    assert _counts(postgres_engine) == (1, 0, 0)


@pytest.mark.parametrize("postgres", [False, True], ids=["sqlite", "postgres"])
def test_audit_failure_rolls_back_instance_job_and_audit(
    postgres: bool,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = request.getfixturevalue("postgres_engine" if postgres else "engine")
    repository = request.getfixturevalue("postgres_repository" if postgres else "repository")
    _seed_instance(engine)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected_audit_failure")

    original_audit_boundary = repository._append_audit_record
    with monkeypatch.context() as audit_failure:
        audit_failure.setattr(repository, "_append_audit_record", fail_audit)
        with pytest.raises(RuntimeError, match="injected_audit_failure"):
            repository.create_job(_command("start", "key-1"), "operator_cli")

    assert repository._append_audit_record == original_audit_boundary

    assert repository.get_instance("instance-1").optimistic_version == 0
    assert repository.list_jobs("instance-1") == ()
    assert _counts(engine) == (1, 0, 0)


@pytest.mark.parametrize(
    ("blocking_status", "expected_code"),
    [
        ("pending", "active_job_exists"),
        ("claimed", "active_job_exists"),
        ("running", "active_job_exists"),
        ("needs_reconciliation", "reconciliation_required"),
    ],
)
def test_create_job_rejects_blocking_job_statuses(
    repository: SqlRuntimeRepository,
    engine: Engine,
    blocking_status: str,
    expected_code: str,
) -> None:
    _seed_instance(engine)
    with Session(engine) as session, session.begin():
        session.add(
            RuntimeLifecycleJobRecord(
                job_id="existing-job",
                instance_id="instance-1",
                requested_action="stop",
                idempotency_key="existing-key",
                expected_instance_version=0,
                status=blocking_status,
                lease_owner="supervisor-1" if blocking_status in {"claimed", "running"} else None,
                lease_expires_at=NOW + timedelta(seconds=30)
                if blocking_status in {"claimed", "running"}
                else None,
                requested_at=NOW,
                started_at=NOW if blocking_status in {"claimed", "running"} else None,
                completed_at=NOW if blocking_status == "needs_reconciliation" else None,
                failure_code="stale_lease" if blocking_status == "needs_reconciliation" else None,
            )
        )

    with pytest.raises(RuntimeConflict, match=rf"^{expected_code}$"):
        repository.create_job(_command("start", "new-key"), "operator_cli")


def test_start_stop_retry_and_retire_apply_exact_transitions(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine, "start-instance")
    start = repository.create_job(
        _command("start", "start-key", instance_id="start-instance"), "operator_cli"
    )
    assert start.status == "pending"
    assert repository.get_instance("start-instance").desired_state == "running"

    _seed_instance(
        engine,
        "stop-instance",
        desired_state="running",
        lifecycle_status="healthy",
    )
    stop = repository.create_job(
        _command("stop", "stop-key", instance_id="stop-instance"), "operator_cli"
    )
    assert stop.status == "pending"
    assert repository.get_instance("stop-instance").desired_state == "stopped"

    _seed_instance(
        engine,
        "retry-instance",
        desired_state="running",
        lifecycle_status="failed",
        failure_latched=True,
    )
    retry = repository.create_job(
        _command("retry", "retry-key", instance_id="retry-instance"), "operator_cli"
    )
    assert retry.status == "pending"
    assert repository.get_instance("retry-instance").failure_latched is False

    _seed_instance(engine, "retire-instance", state_allocation_id="retained-allocation")
    retire = repository.create_job(
        _command("retire", "retire-key", instance_id="retire-instance"), "operator_cli"
    )
    retired = repository.get_instance("retire-instance")
    assert retire.status == "succeeded"
    assert retire.completed_at == NOW
    assert retired.desired_state == "retired"
    assert retired.lifecycle_status == "retired"
    assert retired.retired_at == NOW
    assert retired.state_allocation_id == "retained-allocation"
    assert retired.optimistic_version == 1


def test_stop_without_runtime_work_is_terminal_versioned_no_op(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine)

    job = repository.create_job(_command("stop", "stop-key"), "operator_cli")

    assert job.status == "succeeded"
    assert job.completed_at == NOW
    assert repository.get_instance("instance-1").optimistic_version == 1


@pytest.mark.parametrize(
    ("action", "instance_updates", "active_attempt", "expected_code"),
    [
        ("start", {"desired_state": "running"}, False, "start_requires_stopped"),
        ("start", {"lifecycle_status": "healthy"}, False, "start_requires_stopped"),
        ("start", {"failure_latched": True}, False, "start_failure_latched"),
        ("start", {}, True, "start_active_attempt_exists"),
        (
            "stop",
            {"desired_state": "retired", "lifecycle_status": "retired"},
            False,
            "stop_retired_instance",
        ),
        (
            "retry",
            {"lifecycle_status": "failed", "failure_latched": True},
            False,
            "retry_requires_running",
        ),
        ("retry", {"desired_state": "running"}, False, "retry_requires_failed"),
        (
            "retry",
            {"desired_state": "running", "lifecycle_status": "failed"},
            False,
            "retry_requires_failure_latch",
        ),
        ("retire", {"desired_state": "running"}, False, "retire_requires_stopped"),
        ("retire", {"lifecycle_status": "healthy"}, False, "retire_requires_terminal"),
        ("retire", {}, True, "retire_active_attempt_exists"),
    ],
)
def test_action_rules_fail_closed_without_mutation(
    repository: SqlRuntimeRepository,
    engine: Engine,
    action: str,
    instance_updates: dict[str, object],
    active_attempt: bool,
    expected_code: str,
) -> None:
    _seed_instance(engine, **instance_updates)
    if active_attempt:
        _seed_attempt(engine)

    with pytest.raises(RuntimeInvalidTransition, match=rf"^{expected_code}$"):
        repository.create_job(_command(action, "key-1"), "operator_cli")

    assert repository.get_instance("instance-1").optimistic_version == 0
    assert repository.list_jobs("instance-1") == ()


def test_read_views_are_ordered_and_unknown_instances_fail(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine, "instance-b", created_at=NOW + timedelta(seconds=1))
    _seed_instance(engine, "instance-a", created_at=NOW)
    _seed_attempt(engine, "instance-a", "attempt-2", attempt_number=2, status="stopped")
    _seed_attempt(engine, "instance-a", "attempt-1", attempt_number=1, status="stopped")
    repository.create_job(_command("stop", "key-b", instance_id="instance-b"), "operator_cli")
    repository.create_job(_command("stop", "key-a", instance_id="instance-a"), "operator_cli")

    assert [item.instance_id for item in repository.list_instances()] == [
        "instance-a",
        "instance-b",
    ]
    assert [item.attempt_number for item in repository.list_attempts("instance-a")] == [1, 2]
    assert [item.idempotency_key for item in repository.list_jobs("instance-a")] == ["key-a"]
    for operation in (
        lambda: repository.get_instance("unknown"),
        lambda: repository.list_attempts("unknown"),
        lambda: repository.list_jobs("unknown"),
    ):
        with pytest.raises(RuntimeNotFound, match=r"^runtime_instance_not_found$"):
            operation()


def test_list_attempts_maps_health_evidence_to_result_code_only(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine)
    _seed_attempt(
        engine,
        status="stopped",
        health_result={
            "result_code": "healthy",
            "checks": [{"name": "runtime_http", "status": "passed"}],
        },
    )

    attempt = repository.list_attempts("instance-1")[0]

    assert attempt.health_result == "healthy"
    assert "checks" not in attempt.model_dump_json()


@pytest.mark.parametrize(
    "health_result",
    [
        {"checks": ["private_missing_result"]},
        {"result_code": {"private": "non_string_result"}},
        {"result_code": "INVALID private result"},
    ],
    ids=["missing", "non-string", "invalid-identifier"],
)
def test_list_attempts_rejects_invalid_health_evidence_with_stable_code(
    repository: SqlRuntimeRepository,
    engine: Engine,
    health_result: dict[str, object],
) -> None:
    _seed_instance(engine)
    _seed_attempt(engine, status="stopped", health_result=health_result)

    with pytest.raises(RuntimeDataError) as exc_info:
        repository.list_attempts("instance-1")

    assert str(exc_info.value) == "invalid_health_result"
    assert not any(
        marker in str(exc_info.value)
        for marker in ("private_missing_result", "non_string_result", "INVALID private result")
    )


@pytest.mark.parametrize(
    "record_type,seed_kind,operation,column",
    [
        (
            RuntimeInstanceRecord,
            "instance",
            lambda repository: repository.list_instances(),
            "management_mode",
        ),
        (
            RuntimeAttemptRecord,
            "attempt",
            lambda repository: repository.list_attempts("instance-1"),
            "status",
        ),
        (
            RuntimeLifecycleJobRecord,
            "job",
            lambda repository: repository.list_jobs("instance-1"),
            "status",
        ),
    ],
    ids=["management-mode", "attempt-status", "job-status"],
)
def test_read_views_map_persisted_enum_corruption_to_stable_data_error(
    repository: SqlRuntimeRepository,
    engine: Engine,
    record_type: type,
    seed_kind: str,
    operation,
    column: str,
) -> None:
    if seed_kind == "job":
        _seed_instance(engine)
        repository.create_job(_command("stop", "key-1"), "operator_cli")
    else:
        _seed_instance(engine)
        if seed_kind == "attempt":
            _seed_attempt(engine)
    private_value = "private-corrupt-enum-secret"
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
        connection.execute(record_type.__table__.update().values({column: private_value}))

    with pytest.raises(RuntimeDataError) as exc_info:
        operation(repository)

    assert str(exc_info.value) == "invalid_registry_data"
    assert private_value not in str(exc_info.value)


def test_claim_orders_jobs_and_validates_lease_bounds(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine, "instance-a")
    _seed_instance(engine, "instance-b")
    repository.create_job(_command("start", "key-b", instance_id="instance-b"), "operator_cli")
    repository.create_job(_command("start", "key-a", instance_id="instance-a"), "operator_cli")

    first = repository.claim_next_job("supervisor-a", lease_seconds=30)
    second = repository.claim_next_job("supervisor-b", lease_seconds=30)

    assert first is not None and second is not None
    assert first.requested_at <= second.requested_at
    assert first.status == second.status == "claimed"
    assert first.lease_expires_at == NOW + timedelta(seconds=30)
    assert repository.claim_next_job("supervisor-c", lease_seconds=30) is None
    assert _counts(engine) == (2, 2, 4)
    for invalid in (0, 3601):
        with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_lease_seconds$"):
            repository.claim_next_job("supervisor-c", lease_seconds=invalid)


def test_expired_lease_is_reconciled_before_pending_claim(
    repository: SqlRuntimeRepository,
    engine: Engine,
    clock: MutableClock,
) -> None:
    _seed_instance(engine, "instance-a")
    _seed_instance(engine, "instance-b")
    repository.create_job(_command("start", "key-a", instance_id="instance-a"), "operator_cli")
    repository.create_job(_command("start", "key-b", instance_id="instance-b"), "operator_cli")
    claimed = repository.claim_next_job("supervisor-a", lease_seconds=10)
    assert claimed is not None
    clock.now += timedelta(seconds=11)

    reclaimed = repository.claim_next_job("supervisor-b", lease_seconds=10)

    assert reclaimed is not None
    assert reclaimed.job_id == claimed.job_id
    assert reclaimed.status == "needs_reconciliation"
    assert reclaimed.failure_code == "stale_lease"
    assert reclaimed.completed_at == clock.now
    assert repository.list_jobs("instance-b")[0].status == "pending"
    assert _counts(engine) == (2, 2, 4)


@pytest.mark.parametrize("expired_status", ["claimed", "running"])
def test_postgres_expired_lease_is_reconciled_before_pending_claim(
    postgres_repository: SqlRuntimeRepository,
    postgres_engine: Engine,
    clock: MutableClock,
    expired_status: str,
) -> None:
    _seed_instance(postgres_engine, "instance-a")
    _seed_instance(postgres_engine, "instance-b")
    postgres_repository.create_job(
        _command("start", "key-a", instance_id="instance-a"), "operator_cli"
    )
    pending = postgres_repository.create_job(
        _command("start", "key-b", instance_id="instance-b"), "operator_cli"
    )
    claimed = postgres_repository.claim_next_job("supervisor-a", lease_seconds=10)
    assert claimed is not None
    if expired_status == "running":
        with Session(postgres_engine) as session, session.begin():
            record = session.get(RuntimeLifecycleJobRecord, claimed.job_id)
            assert record is not None
            record.status = "running"
    clock.now += timedelta(seconds=11)

    reconciled = postgres_repository.claim_next_job("supervisor-b", lease_seconds=10)

    assert reconciled is not None
    assert reconciled.job_id == claimed.job_id
    assert reconciled.status == "needs_reconciliation"
    assert reconciled.failure_code == "stale_lease"
    assert reconciled.completed_at == clock.now
    with Session(postgres_engine) as session:
        expired_record = session.get(RuntimeLifecycleJobRecord, claimed.job_id)
        pending_record = session.get(RuntimeLifecycleJobRecord, pending.job_id)
        assert expired_record is not None
        assert expired_record.status == "needs_reconciliation"
        assert expired_record.failure_code == "stale_lease"
        assert expired_record.lease_owner is None
        assert expired_record.lease_expires_at is None
        assert pending_record is not None
        assert pending_record.status == "pending"
    assert _counts(postgres_engine) == (2, 2, 4)


def test_complete_job_enforces_failure_code_and_late_completion(
    repository: SqlRuntimeRepository,
    engine: Engine,
    clock: MutableClock,
) -> None:
    _seed_instance(engine)
    repository.create_job(_command("start", "key-1"), "operator_cli")
    claimed = repository.claim_next_job("supervisor-a", lease_seconds=10)
    assert claimed is not None

    with pytest.raises(RuntimeInvalidTransition, match=r"^success_failure_code_forbidden$"):
        repository.complete_job(
            claimed.job_id, "succeeded", "failure", "supervisor-a", claimed.lease_generation
        )
    with pytest.raises(RuntimeInvalidTransition, match=r"^failure_code_required$"):
        repository.complete_job(
            claimed.job_id, "failed", None, "supervisor-a", claimed.lease_generation
        )
    with pytest.raises(RuntimeInvalidTransition, match=r"^invalid_completion_status$"):
        repository.complete_job(
            claimed.job_id, "running", None, "supervisor-a", claimed.lease_generation
        )

    clock.now += timedelta(seconds=11)
    completed = repository.complete_job(
        claimed.job_id, "succeeded", None, "supervisor-a", claimed.lease_generation
    )
    assert completed.status == "needs_reconciliation"
    assert completed.failure_code == "stale_lease"
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None
    assert _counts(engine) == (1, 1, 3)


@pytest.mark.parametrize(
    ("requested_status", "requested_failure_code"),
    [("succeeded", None), ("failed", "launch_failed")],
)
def test_postgres_late_completion_requires_reconciliation(
    postgres_repository: SqlRuntimeRepository,
    postgres_engine: Engine,
    clock: MutableClock,
    requested_status: CompletionStatus,
    requested_failure_code: str | None,
) -> None:
    _seed_instance(postgres_engine)
    postgres_repository.create_job(_command("start", "key-1"), "operator_cli")
    claimed = postgres_repository.claim_next_job("supervisor-a", lease_seconds=10)
    assert claimed is not None
    clock.now += timedelta(seconds=11)

    completed = postgres_repository.complete_job(
        claimed.job_id,
        requested_status,
        requested_failure_code,
        "supervisor-a",
        claimed.lease_generation,
    )

    assert completed.status == "needs_reconciliation"
    assert completed.failure_code == "stale_lease"
    assert completed.completed_at == clock.now
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None
    with Session(postgres_engine) as session:
        record = session.get(RuntimeLifecycleJobRecord, claimed.job_id)
        assert record is not None
        assert record.status == "needs_reconciliation"
        assert record.failure_code == "stale_lease"
        assert record.completed_at == clock.now
        assert record.lease_owner is None
        assert record.lease_expires_at is None
    assert _counts(postgres_engine) == (1, 1, 3)


def test_success_and_failure_completion_are_terminal(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine, "success-instance")
    _seed_instance(engine, "failure-instance")
    repository.create_job(
        _command("start", "success-key", instance_id="success-instance"), "operator_cli"
    )
    repository.create_job(
        _command("start", "failure-key", instance_id="failure-instance"), "operator_cli"
    )
    first = repository.claim_next_job("supervisor-a", 30)
    second = repository.claim_next_job("supervisor-b", 30)
    assert first is not None and second is not None

    succeeded = repository.complete_job(
        first.job_id, "succeeded", None, "supervisor-a", first.lease_generation
    )
    failed = repository.complete_job(
        second.job_id, "failed", "launch_failed", "supervisor-b", second.lease_generation
    )

    assert succeeded.status == "succeeded" and succeeded.failure_code is None
    assert failed.status == "failed" and failed.failure_code == "launch_failed"
    with pytest.raises(RuntimeInvalidTransition, match=r"^job_not_completable$"):
        repository.complete_job(
            first.job_id, "succeeded", None, "supervisor-a", first.lease_generation
        )


def test_append_audit_accepts_only_closed_non_secret_input(
    repository: SqlRuntimeRepository,
    engine: Engine,
) -> None:
    _seed_instance(engine)
    state = RuntimeInstanceAuditState(
        desired_state="stopped",
        lifecycle_status="registered",
        failure_latched=False,
        optimistic_version=0,
    )
    event = RuntimeAuditEvent(
        actor_type="operator_cli",
        request_id="request-1",
        idempotency_key=None,
        owner_kind="paper_probe",
        owner_id="owner-1",
        owner_revision="owner-revision-1",
        instance_id="instance-1",
        runtime_spec_revision_id=RUNTIME_SPEC_REVISION_ID,
        adapter_template_revision_id=None,
        action="start",
        previous_state=state,
        next_state=state,
        result_code="accepted",
    )

    repository.append_audit(event)

    with Session(engine) as session:
        record = session.scalar(select(RuntimeAuditEventRecord))
    assert record is not None
    assert record.provenance == {"source": "runtime_repository"}
    assert set(record.previous_state) == set(type(state).model_fields)
    for forbidden in (
        "provenance",
        "body",
        "headers",
        "credential",
        "token",
        "path",
        "dsn",
        "secret_version",
    ):
        with pytest.raises(ValidationError):
            RuntimeAuditEvent.model_validate({**event.model_dump(), forbidden: "forbidden"})


def test_postgres_claimer_skips_a_locked_oldest_job(
    postgres_repository: SqlRuntimeRepository,
    postgres_engine: Engine,
) -> None:
    _seed_instance(postgres_engine, "instance-a")
    _seed_instance(postgres_engine, "instance-b")
    first = postgres_repository.create_job(
        _command("start", "key-a", instance_id="instance-a"), "operator_cli"
    )
    second = postgres_repository.create_job(
        _command("start", "key-b", instance_id="instance-b"), "operator_cli"
    )

    with Session(postgres_engine) as locking_session, locking_session.begin():
        locking_session.scalar(
            select(RuntimeLifecycleJobRecord)
            .where(RuntimeLifecycleJobRecord.job_id == first.job_id)
            .with_for_update()
        )
        claimed = postgres_repository.claim_next_job("supervisor-b", lease_seconds=30)

    assert claimed is not None
    assert claimed.job_id == second.job_id
    with Session(postgres_engine) as persisted_session:
        first_record = persisted_session.get(RuntimeLifecycleJobRecord, first.job_id)
        claimed_record = persisted_session.get(RuntimeLifecycleJobRecord, claimed.job_id)
        audits = persisted_session.scalars(select(RuntimeAuditEventRecord)).all()

        assert first_record is not None
        assert first_record.status == "pending"
        assert first_record.lease_owner is None
        assert first_record.lease_expires_at is None
        assert claimed_record is not None
        assert claimed_record.status == "claimed"
        assert claimed_record.lease_owner == "supervisor-b"
        assert claimed_record.lease_expires_at == NOW + timedelta(seconds=30)
        assert len(audits) == 3
        assert [audit.result_code for audit in audits].count("accepted") == 2
        claim_audits = [audit for audit in audits if audit.result_code == "claimed"]
        assert len(claim_audits) == 1
        assert claim_audits[0].request_id == claimed.job_id
        assert claim_audits[0].action == claimed.requested_action
        assert claim_audits[0].result_code == "claimed"
