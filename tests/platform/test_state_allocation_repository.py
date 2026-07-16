from collections.abc import Iterator
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_domain import RuntimeJobView
from freqtrade.platform.runtime_models import (
    RuntimeInstanceRecord,
    RuntimeLifecycleJobRecord,
)
from freqtrade.platform.runtime_repository import (
    RuntimeDataError,
    RuntimeInvalidTransition,
    SqlRuntimeRepository,
    StateAllocationPreparationMaterial,
)
from freqtrade.platform.template_models import StateAllocationRecord
from tests.platform.test_supervisor_repository import (
    NOW,
    MutableClock,
    SequentialIds,
    _command,
    _lease_args,
    _seed_active_attempt,
    _seed_instance,
)


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
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def repository(engine: Engine, clock: MutableClock) -> SqlRuntimeRepository:
    _seed_instance(engine)
    _set_allocation(engine, status="reserved", ready_at=None)
    return SqlRuntimeRepository(engine, clock=clock, id_factory=SequentialIds())


@pytest.fixture
def running_job(repository: SqlRuntimeRepository):
    repository.create_job(_command("start", "state-start"), "operator_cli")
    job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert job is not None
    return job


def _set_allocation(engine: Engine, **values: object) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            update(StateAllocationRecord)
            .where(StateAllocationRecord.state_allocation_id == "state-allocation-1")
            .values(**values)
        )


def _allocation(engine: Engine) -> StateAllocationRecord:
    with Session(engine) as session:
        record = session.get(StateAllocationRecord, "state-allocation-1")
        assert record is not None
        session.expunge(record)
        return record


def _begin(
    repository: SqlRuntimeRepository,
    job: RuntimeJobView,
) -> StateAllocationPreparationMaterial:
    return repository.begin_state_provisioning(job.job_id, **_lease_args(job))


def _allocation_state(engine: Engine) -> tuple[object, ...]:
    record = _allocation(engine)
    return tuple(
        getattr(record, column.name) for column in StateAllocationRecord.__table__.columns
    )


def test_state_preparation_material_is_typed_frozen_and_keeps_paths_out_of_launch_authority(
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    material = _begin(repository, running_job)

    assert material == StateAllocationPreparationMaterial(
        state_allocation_id="state-allocation-1",
        instance_id="instance-1",
        layout_id="fixture-layout-1",
        provider_id="managed-local-v1",
        relative_path="ft_userdata/runtime/instances/instance-1",
        kind="fresh",
        status="provisioning",
        generation=1,
        restore_source_bundle_id=None,
        created_at=NOW,
        ready_at=None,
        retired_at=None,
    )
    with pytest.raises(ValidationError):
        StateAllocationPreparationMaterial.model_validate(
            {**material.model_dump(), "host_path": "C:/unsafe"}
        )
    for changes in (
        {"relative_path": "ft_userdata/runtime/instances/other-instance"},
        {"kind": "restored", "restore_source_bundle_id": None},
        {"kind": "fresh", "restore_source_bundle_id": "restore-bundle-1"},
    ):
        with pytest.raises(ValidationError):
            StateAllocationPreparationMaterial.model_validate(
                {**material.model_dump(), **changes}
            )
    with pytest.raises(ValidationError):
        material.status = "ready"


def test_begin_state_provisioning_is_idempotent_for_provisioning_and_ready(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    first = _begin(repository, running_job)
    clock.now = NOW + timedelta(seconds=10)
    second = _begin(repository, running_job)

    assert first == second
    _set_allocation(engine, status="ready", ready_at=NOW)
    ready = _begin(repository, running_job)
    assert ready.status == "ready"
    assert ready.ready_at == NOW
    assert ready.generation == 1


def test_begin_state_provisioning_returns_correlated_restore_source(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    _set_allocation(
        engine,
        kind="restored",
        restore_source_bundle_id="restore-bundle-1",
    )

    material = _begin(repository, running_job)

    assert material.kind == "restored"
    assert material.restore_source_bundle_id == "restore-bundle-1"


def test_complete_state_provisioning_sets_ready_timestamp_once(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    provisioning = _begin(repository, running_job)
    clock.now = NOW + timedelta(seconds=5)

    ready = repository.complete_state_provisioning(
        running_job.job_id,
        provisioning.state_allocation_id,
        provisioning.generation,
        **_lease_args(running_job),
    )
    clock.now = NOW + timedelta(seconds=20)
    repeated = repository.complete_state_provisioning(
        running_job.job_id,
        ready.state_allocation_id,
        ready.generation,
        **_lease_args(running_job),
    )

    assert ready.status == "ready"
    assert ready.ready_at == NOW + timedelta(seconds=5)
    assert repeated == ready
    persisted_ready_at = _allocation(engine).ready_at
    assert persisted_ready_at is not None
    assert persisted_ready_at.replace(tzinfo=NOW.tzinfo) == NOW + timedelta(seconds=5)


@pytest.mark.parametrize("initial_status", ["provisioning", "ready"])
def test_quarantine_state_allocation_clears_ready_timestamp_and_is_idempotent(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    initial_status: str,
) -> None:
    _set_allocation(
        engine,
        status=initial_status,
        ready_at=NOW if initial_status == "ready" else None,
    )
    quarantined = repository.quarantine_state_allocation(
        running_job.job_id,
        "state-allocation-1",
        1,
        **_lease_args(running_job),
    )
    repeated = repository.quarantine_state_allocation(
        running_job.job_id,
        "state-allocation-1",
        1,
        **_lease_args(running_job),
    )

    assert quarantined.status == "quarantined"
    assert quarantined.ready_at is None
    assert repeated == quarantined
    assert _allocation(engine).ready_at is None


@pytest.mark.parametrize(
    ("operation", "initial_status"),
    [
        ("begin", "quarantined"),
        ("begin", "retired"),
        ("complete", "reserved"),
        ("complete", "quarantined"),
        ("complete", "retired"),
        ("quarantine", "reserved"),
        ("quarantine", "retired"),
    ],
)
def test_state_preparation_rejects_unapproved_transitions_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    operation: str,
    initial_status: str,
) -> None:
    _set_allocation(
        engine,
        status=initial_status,
        ready_at=None,
        retired_at=NOW if initial_status == "retired" else None,
    )
    before = _allocation_state(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^state_allocation_not_transitionable$"):
        if operation == "begin":
            _begin(repository, running_job)
        elif operation == "complete":
            repository.complete_state_provisioning(
                running_job.job_id,
                "state-allocation-1",
                1,
                **_lease_args(running_job),
            )
        else:
            repository.quarantine_state_allocation(
                running_job.job_id,
                "state-allocation-1",
                1,
                **_lease_args(running_job),
            )

    assert _allocation_state(engine) == before


@pytest.mark.parametrize(
    ("allocation_id", "generation", "error"),
    [
        ("other-allocation", 1, "state_allocation_mismatch"),
        ("state-allocation-1", 2, "state_allocation_generation_mismatch"),
    ],
)
def test_complete_state_provisioning_fences_allocation_identity_and_generation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    allocation_id: str,
    generation: int,
    error: str,
) -> None:
    _begin(repository, running_job)
    before = _allocation_state(engine)

    with pytest.raises(RuntimeInvalidTransition, match=rf"^{error}$"):
        repository.complete_state_provisioning(
            running_job.job_id,
            allocation_id,
            generation,
            **_lease_args(running_job),
        )

    assert _allocation_state(engine) == before


@pytest.mark.parametrize(
    ("lease_owner", "lease_generation", "error"),
    [
        ("other-supervisor", 1, "lease_owner_mismatch"),
        ("supervisor-1", 2, "lease_generation_mismatch"),
    ],
)
def test_state_preparation_is_lease_fenced_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    lease_owner: str,
    lease_generation: int,
    error: str,
) -> None:
    before = _allocation_state(engine)

    with pytest.raises(RuntimeInvalidTransition, match=rf"^{error}$"):
        repository.begin_state_provisioning(
            running_job.job_id,
            lease_owner,
            lease_generation,
        )

    assert _allocation_state(engine) == before


def test_state_preparation_rejects_expired_lease_without_mutation(
    engine: Engine,
    clock: MutableClock,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    before = _allocation_state(engine)
    clock.now = NOW + timedelta(seconds=31)

    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
        _begin(repository, running_job)

    assert _allocation_state(engine) == before


def test_state_preparation_rejects_terminal_job_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    repository.complete_job(
        running_job.job_id,
        "succeeded",
        None,
        **_lease_args(running_job),
    )
    before = _allocation_state(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^job_not_leased$"):
        _begin(repository, running_job)

    assert _allocation_state(engine) == before


def test_state_preparation_rejects_an_active_attempt_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    _seed_active_attempt(engine, "instance-1", "a")
    before = _allocation_state(engine)

    with pytest.raises(RuntimeInvalidTransition, match=r"^state_preparation_active_attempt$"):
        _begin(repository, running_job)

    assert _allocation_state(engine) == before


@pytest.mark.parametrize(
    "corruption",
    [
        {"relative_path": "ft_userdata/runtime/instances/other-instance"},
        {"provider_id": "foreign-provider"},
        {"layout_id": "foreign-layout"},
        {"kind": "restored", "restore_source_bundle_id": None},
        {"kind": "fresh", "restore_source_bundle_id": "restore-bundle-1"},
    ],
)
def test_state_preparation_rejects_corrupted_allocation_identity_without_adoption(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    corruption: dict[str, object],
) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            update(StateAllocationRecord)
            .where(StateAllocationRecord.state_allocation_id == "state-allocation-1")
            .values(**corruption)
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = OFF")
    before = _allocation_state(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^state_allocation_correlation_mismatch$",
    ):
        _begin(repository, running_job)

    assert _allocation_state(engine) == before


@pytest.mark.parametrize("operation", ["begin", "complete", "quarantine"])
@pytest.mark.parametrize(
    "corruption",
    [
        {"status": "provisioning", "ready_at": NOW},
        {"status": "ready", "ready_at": None},
    ],
)
def test_every_state_transition_rejects_corrupted_current_status_timestamp_tuple(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    operation: str,
    corruption: dict[str, object],
) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            update(StateAllocationRecord)
            .where(StateAllocationRecord.state_allocation_id == "state-allocation-1")
            .values(**corruption)
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = OFF")
    before = _allocation_state(engine)

    with pytest.raises(RuntimeDataError, match=r"^invalid_registry_data$"):
        if operation == "begin":
            _begin(repository, running_job)
        elif operation == "complete":
            repository.complete_state_provisioning(
                running_job.job_id,
                "state-allocation-1",
                1,
                **_lease_args(running_job),
            )
        else:
            repository.quarantine_state_allocation(
                running_job.job_id,
                "state-allocation-1",
                1,
                **_lease_args(running_job),
            )

    assert _allocation_state(engine) == before


@pytest.mark.parametrize(
    "instance_drift",
    [
        {
            "desired_state": "retired",
            "lifecycle_status": "retired",
            "retired_at": NOW,
        },
        {"desired_state": "stopped"},
        {"lifecycle_status": "starting"},
        {"failure_latched": True},
    ],
)
def test_state_preparation_rejects_instance_drift_after_job_claim_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
    instance_drift: dict[str, object],
) -> None:
    with Session(engine) as session, session.begin():
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        assert instance is not None
        for field_name, value in instance_drift.items():
            setattr(instance, field_name, value)
    before = _allocation_state(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^state_preparation_instance_not_launchable$",
    ):
        _begin(repository, running_job)

    assert _allocation_state(engine) == before


def test_retry_job_accepts_only_the_explicit_failed_instance_state(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    with Session(engine) as session, session.begin():
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        assert instance is not None
        instance.desired_state = "running"
        instance.lifecycle_status = "failed"
        instance.failure_latched = True
    repository.create_job(_command("retry", "state-retry"), "operator_cli")
    job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert job is not None

    material = _begin(repository, job)

    assert material.status == "provisioning"


def test_retry_job_rejects_lifecycle_drift_after_claim_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    with Session(engine) as session, session.begin():
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        assert instance is not None
        instance.desired_state = "running"
        instance.lifecycle_status = "failed"
        instance.failure_latched = True
    repository.create_job(_command("retry", "state-retry-drift"), "operator_cli")
    job = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert job is not None
    with Session(engine) as session, session.begin():
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        assert instance is not None
        instance.lifecycle_status = "registered"
    before = _allocation_state(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^state_preparation_instance_not_launchable$",
    ):
        _begin(repository, job)

    assert _allocation_state(engine) == before


def test_state_preparation_rejects_running_job_without_active_attempt(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    with Session(engine) as session, session.begin():
        job = session.get(RuntimeLifecycleJobRecord, running_job.job_id)
        assert job is not None
        job.status = "running"
    before = _allocation_state(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^state_preparation_job_not_claimed$",
    ):
        _begin(repository, running_job)

    assert _allocation_state(engine) == before


def test_state_preparation_rejects_optimistic_version_drift_without_mutation(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    with Session(engine) as session, session.begin():
        instance = session.get(RuntimeInstanceRecord, "instance-1")
        assert instance is not None
        instance.optimistic_version += 1
    before = _allocation_state(engine)

    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^state_preparation_instance_version_mismatch$",
    ):
        _begin(repository, running_job)

    assert _allocation_state(engine) == before


def test_state_preparation_locks_job_instance_allocation_then_active_attempt(
    monkeypatch: pytest.MonkeyPatch,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    statements: list[str] = []
    original_scalar = Session.scalar

    def record_scalar(
        session: Session,
        statement: object,
        *args: object,
        **kwargs: object,
    ):
        if hasattr(statement, "compile"):
            statements.append(str(statement.compile(dialect=postgresql.dialect())).upper())
        return original_scalar(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", record_scalar)

    _begin(repository, running_job)

    table_names = (
        "RUNTIME_LIFECYCLE_JOBS",
        "RUNTIME_INSTANCES",
        "STATE_ALLOCATIONS",
        "RUNTIME_ATTEMPTS",
    )
    locked = [
        next(
            statement
            for statement in statements
            if f"FROM {table_name}" in statement and "FOR UPDATE" in statement
        )
        for table_name in table_names
    ]
    assert [statements.index(statement) for statement in locked] == sorted(
        statements.index(statement) for statement in locked
    )


def test_state_preparation_updates_only_status_and_ready_at_columns(
    engine: Engine,
    repository: SqlRuntimeRepository,
    running_job,
) -> None:
    statements: list[str] = []

    def record_updates(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = statement.upper()
        if normalized.startswith("UPDATE STATE_ALLOCATIONS SET "):
            statements.append(normalized)

    event.listen(engine, "before_cursor_execute", record_updates)
    try:
        provisioning = _begin(repository, running_job)
        ready = repository.complete_state_provisioning(
            running_job.job_id,
            provisioning.state_allocation_id,
            provisioning.generation,
            **_lease_args(running_job),
        )
        repository.quarantine_state_allocation(
            running_job.job_id,
            ready.state_allocation_id,
            ready.generation,
            **_lease_args(running_job),
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_updates)

    assert len(statements) == 3
    assignments = [
        statement.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        for statement in statements
    ]
    assert assignments[0] == "STATUS=?"
    assert set(assignments[1].split(", ")) == {"STATUS=?", "READY_AT=?"}
    assert set(assignments[2].split(", ")) == {"STATUS=?", "READY_AT=?"}
