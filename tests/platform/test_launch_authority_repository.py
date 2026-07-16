import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, update
from sqlalchemy.orm import Session

import freqtrade.platform as platform
from freqtrade.markets.catalog import ProductType
from freqtrade.markets.instrument import MarketType
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_domain import (
    RuntimeJobView,
    RuntimeLifecycleCommand,
    RuntimeOwnerRef,
)
from freqtrade.platform.runtime_models import RuntimeInstanceRecord, RuntimeLifecycleJobRecord
from freqtrade.platform.runtime_repository import (
    PersistedLaunchAuthority,
    RuntimeDataError,
    RuntimeInvalidTransition,
    RuntimeNotFound,
    RuntimeRepository,
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


NOW = datetime(2026, 7, 17, 8, tzinfo=UTC)
INSTANCE_ID = "instance-1"
ROOT_COMMIT = "1" * 40
BACKEND_COMMIT = "2" * 40
FRONTEND_COMMIT = "3" * 40
STRATEGIES_COMMIT = "4" * 40
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
    secret_classes=("exchange_credentials", "jwt_secret"),
    state_layout_id="fixture-layout-1",
)
TEMPLATE_CANONICAL_PAYLOAD = json.dumps(
    {"schema_version": 1, **TEMPLATE.model_dump(mode="json")},
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
) + "\n"
TEMPLATE_DIGEST = hashlib.sha256(TEMPLATE_CANONICAL_PAYLOAD.encode()).hexdigest()
TEMPLATE_REVISION_ID = f"template-{TEMPLATE_DIGEST}"
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
        template_digest=TEMPLATE_DIGEST,
        image_policy_id="reviewed-image-v1",
        command_policy_id="fixed-command-v1",
        mount_policy_ids=("runtime-mounts-v1",),
        network_policy_id="private-network-v1",
        health_profile_id="api-ping-v1",
        resource_profile_id="paper-small-v1",
        state_layout_id="fixture-layout-1",
        state_allocation_id="state-allocation-1",
        secret_reference_ids=("jwt", "exchange"),
        config_blob_commit=ROOT_COMMIT,
        strategy_commit=STRATEGIES_COMMIT,
        safety_policy_commit=ROOT_COMMIT,
        root_commit=ROOT_COMMIT,
        backend_commit=BACKEND_COMMIT,
        frontend_commit=FRONTEND_COMMIT,
        strategies_commit=STRATEGIES_COMMIT,
        config_blob_digest="5" * 64,
        strategy_digest="6" * 64,
        safety_policy_digest="7" * 64,
    )
)


class MutableClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def engine() -> Iterator[Engine]:
    value = create_engine("sqlite+pysqlite:///:memory:")
    PlatformBase.metadata.create_all(value)
    with value.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_secret_version_active")
    try:
        yield value
    finally:
        value.dispose()


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def repository(engine: Engine, clock: MutableClock) -> SqlRuntimeRepository:
    return SqlRuntimeRepository(engine, clock=clock)


def _seed_launch_authority(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
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
                    canonical_payload=TEMPLATE_CANONICAL_PAYLOAD,
                    payload_digest=TEMPLATE_DIGEST,
                    source_commit=ROOT_COMMIT,
                    root_commit=ROOT_COMMIT,
                    backend_commit=BACKEND_COMMIT,
                    frontend_commit=FRONTEND_COMMIT,
                    strategies_commit=STRATEGIES_COMMIT,
                    status="active",
                    published_by="platform-test",
                    published_at=NOW,
                    deprecated_at=None,
                    revoked_at=None,
                ),
                StateAllocationRecord(
                    state_allocation_id="state-allocation-1",
                    instance_id=INSTANCE_ID,
                    layout_id="fixture-layout-1",
                    provider_id="managed-local-v1",
                    relative_path=f"ft_userdata/runtime/instances/{INSTANCE_ID}",
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
                SecretReferenceRecord(
                    secret_reference_id="jwt",
                    provider_id="local-file-v1",
                    secret_class="jwt_secret",
                    logical_name="paper-jwt",
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
                    runtime_spec_revision_id=RUNTIME_SPEC.runtime_spec_revision_id,
                    owner_kind="paper_probe",
                    owner_id="owner-1",
                    owner_revision="owner-revision-1",
                    instance_kind="execution_worker",
                    catalog_revision_id="catalog-revision-1",
                    environment="paper",
                    adapter_template_revision_id=TEMPLATE_REVISION_ID,
                    state_allocation_id="state-allocation-1",
                    canonical_payload=RUNTIME_SPEC.canonical_payload,
                    payload_digest=RUNTIME_SPEC.payload_digest,
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
                SecretVersionMetadataRecord(
                    secret_reference_id="jwt",
                    version_id="secret-version-2",
                    status="active",
                    created_at=NOW,
                    activated_at=NOW,
                    retired_at=None,
                ),
            )
        )
        session.flush()
        session.add(
            RuntimeInstanceRecord(
                instance_id=INSTANCE_ID,
                instance_kind="execution_worker",
                owner_kind="paper_probe",
                owner_id="owner-1",
                owner_revision="owner-revision-1",
                management_mode="supervisor",
                runtime_spec_revision_id=RUNTIME_SPEC.runtime_spec_revision_id,
                environment="paper",
                state_allocation_id="state-allocation-1",
                desired_state="stopped",
                lifecycle_status="registered",
                failure_latched=False,
                optimistic_version=7,
                created_at=NOW,
                retired_at=None,
            )
        )


def _update(engine: Engine, record_type: type[PlatformBase], **values: object) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        try:
            connection.execute(update(record_type).values(**values))
        finally:
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")


def _claim_start(repository: SqlRuntimeRepository) -> tuple[RuntimeJobView, str]:
    job = repository.create_job(
        RuntimeLifecycleCommand(
            instance_id=INSTANCE_ID,
            action="start",
            idempotency_key="start-1",
            expected_instance_version=7,
        ),
        "operator_cli",
    )
    claimed = repository.claim_next_job("supervisor-1", lease_seconds=30)
    assert claimed is not None
    assert claimed.job_id == job.job_id
    attempt_id = repository.prepare_attempt_id(
        claimed.job_id,
        "supervisor-1",
        claimed.lease_generation,
    )
    return claimed, attempt_id


def test_launch_authority_is_immutable_complete_and_loaded_by_one_authority_join(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        authority = repository.resolve_launch_authority_material(
            job.job_id,
            attempt_id,
            "supervisor-1",
            job.lease_generation,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert isinstance(authority, PersistedLaunchAuthority)
    assert authority.instance.instance_id == INSTANCE_ID
    assert authority.instance.optimistic_version == 8
    assert authority.runtime_spec.runtime_spec_revision_id == RUNTIME_SPEC.runtime_spec_revision_id
    assert authority.runtime_spec.canonical_payload == RUNTIME_SPEC.canonical_payload
    assert authority.adapter_template.adapter_template_revision_id == TEMPLATE_REVISION_ID
    assert authority.adapter_template.canonical_payload == TEMPLATE_CANONICAL_PAYLOAD
    assert authority.state_allocation.state_allocation_id == "state-allocation-1"
    assert tuple(item.secret_reference_id for item in authority.secret_references) == (
        "exchange",
        "jwt",
    )
    assert authority.secret_references[0].active_version_id == "secret-version-1"
    authority_statements = [
        statement
        for statement in statements
        if "LEFT OUTER JOIN runtime_spec_revisions" in statement
    ]
    assert len(authority_statements) == 1
    assert len(statements) > len(authority_statements)
    serialized = authority.model_dump(mode="json")
    assert "relative_path" not in str(serialized)
    assert "secret_value" not in str(serialized)
    with pytest.raises(ValidationError):
        authority.instance_id = "replacement"  # type: ignore[attr-defined,misc]


def test_launch_authority_ignores_same_owner_reference_for_another_instance(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    with Session(engine) as session, session.begin():
        session.add(
            SecretReferenceRecord(
                secret_reference_id="other-instance-secret",
                provider_id="local-file-v1",
                secret_class="other_credentials",
                logical_name="other-instance-exchange",
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
                secret_reference_id="other-instance-secret",
                version_id="other-secret-version-1",
                status="active",
                created_at=NOW,
                activated_at=NOW,
                retired_at=None,
            )
        )
    job, attempt_id = _claim_start(repository)

    authority = repository.resolve_launch_authority_material(
        job.job_id,
        attempt_id,
        "supervisor-1",
        job.lease_generation,
    )

    assert tuple(item.secret_reference_id for item in authority.secret_references) == (
        "exchange",
        "jwt",
    )


def test_launch_authority_contract_is_public(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)

    authority = repository.resolve_launch_authority_material(
        job.job_id,
        attempt_id,
        "supervisor-1",
        job.lease_generation,
    )
    assert isinstance(authority, PersistedLaunchAuthority)
    assert isinstance(repository, RuntimeRepository)
    assert "PersistedLaunchAuthority" in platform.__all__


def test_launch_authority_rejects_unknown_job(repository: SqlRuntimeRepository) -> None:
    with pytest.raises(RuntimeNotFound, match=r"^runtime_job_not_found$"):
        repository.resolve_launch_authority_material(
            "missing-job",
            "attempt-missing",
            "supervisor-1",
            1,
        )


@pytest.mark.parametrize(
    ("record_type", "values"),
    [
        (RuntimeInstanceRecord, {"owner_id": "foreign-owner"}),
        (RuntimeInstanceRecord, {"management_mode": "legacy_compose"}),
        (RuntimeInstanceRecord, {"desired_state": "retired", "retired_at": NOW}),
        (RuntimeInstanceRecord, {"runtime_spec_revision_id": "missing-spec"}),
        (RuntimeSpecRevisionRecord, {"canonical_payload": "{}"}),
        (RuntimeSpecRevisionRecord, {"owner_id": "foreign-owner"}),
        (RuntimeSpecRevisionRecord, {"adapter_template_revision_id": "missing-template"}),
        (RuntimeSpecRevisionRecord, {"state_allocation_id": "missing-allocation"}),
        (AdapterTemplateRevisionRecord, {"status": "revoked", "revoked_at": NOW}),
        (AdapterTemplateRevisionRecord, {"canonical_payload": "{}"}),
        (AdapterTemplateRevisionRecord, {"payload_digest": "8" * 64}),
        (AdapterTemplateRevisionRecord, {"backend_commit": "9" * 40}),
        (StateAllocationRecord, {"instance_id": "foreign-instance"}),
        (StateAllocationRecord, {"layout_id": "foreign-layout"}),
        (StateAllocationRecord, {"provider_id": "foreign-provider"}),
        (StateAllocationRecord, {"generation": 0}),
        (StateAllocationRecord, {"status": "quarantined"}),
        (SecretReferenceRecord, {"owner_id": "foreign-owner"}),
        (SecretReferenceRecord, {"provider_id": "foreign-provider"}),
        (SecretReferenceRecord, {"secret_class": "foreign-class"}),
        (SecretReferenceRecord, {"status": "disabled"}),
        (SecretVersionMetadataRecord, {"status": "retired", "retired_at": NOW}),
    ],
    ids=[
        "instance-owner",
        "management-mode",
        "retired-instance",
        "missing-runtime-spec",
        "runtime-spec-envelope",
        "runtime-spec-owner",
        "missing-template",
        "missing-allocation",
        "revoked-template",
        "template-envelope",
        "template-digest",
        "component-commit",
        "state-instance",
        "state-layout",
        "state-provider",
        "state-generation",
        "state-status",
        "secret-owner",
        "secret-provider",
        "secret-class",
        "secret-status",
        "active-version-missing",
    ],
)
def test_launch_authority_rejects_correlated_authority_drift(
    engine: Engine,
    repository: SqlRuntimeRepository,
    record_type: type[PlatformBase],
    values: dict[str, object],
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)
    _update(engine, record_type, **values)

    with pytest.raises(RuntimeDataError, match=r"^invalid_launch_authority$"):
        repository.resolve_launch_authority_material(
            job.job_id,
            attempt_id,
            "supervisor-1",
            job.lease_generation,
        )


def test_launch_authority_rejects_more_than_one_active_version(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)
    with Session(engine) as session, session.begin():
        session.add(
            SecretVersionMetadataRecord(
                secret_reference_id="exchange",
                version_id="secret-version-3",
                status="active",
                created_at=NOW,
                activated_at=NOW,
                retired_at=None,
            )
        )

    with pytest.raises(RuntimeDataError, match=r"^invalid_launch_authority$"):
        repository.resolve_launch_authority_material(
            job.job_id,
            attempt_id,
            "supervisor-1",
            job.lease_generation,
        )


def test_prepared_attempt_identity_is_idempotent_and_changes_with_generation(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    job, first = _claim_start(repository)

    replay = repository.prepare_attempt_id(
        job.job_id,
        "supervisor-1",
        job.lease_generation,
    )
    _update(engine, RuntimeLifecycleJobRecord, lease_generation=job.lease_generation + 1)
    next_generation = repository.prepare_attempt_id(
        job.job_id,
        "supervisor-1",
        job.lease_generation + 1,
    )

    assert replay == first
    assert next_generation != first


@pytest.mark.parametrize(
    ("lease_owner", "generation", "error"),
    [
        ("wrong-supervisor", 1, "lease_owner_mismatch"),
        ("supervisor-1", 2, "lease_generation_mismatch"),
    ],
    ids=["stale-owner", "stale-generation"],
)
def test_launch_authority_requires_exact_lease_identity(
    engine: Engine,
    repository: SqlRuntimeRepository,
    lease_owner: str,
    generation: int,
    error: str,
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)

    with pytest.raises(RuntimeInvalidTransition, match=rf"^{error}$"):
        repository.resolve_launch_authority_material(
            job.job_id,
            attempt_id,
            lease_owner,
            generation,
        )


def test_launch_authority_rejects_expired_lease(
    engine: Engine,
    repository: SqlRuntimeRepository,
    clock: MutableClock,
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)
    clock.now += timedelta(seconds=31)

    with pytest.raises(RuntimeInvalidTransition, match=r"^lease_expired$"):
        repository.resolve_launch_authority_material(
            job.job_id,
            attempt_id,
            "supervisor-1",
            job.lease_generation,
        )


def test_launch_authority_rejects_wrong_attempt_identity(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    job, _ = _claim_start(repository)

    with pytest.raises(RuntimeInvalidTransition, match=r"^attempt_id_mismatch$"):
        repository.resolve_launch_authority_material(
            job.job_id,
            "attempt-wrong",
            "supervisor-1",
            job.lease_generation,
        )


def test_launch_authority_attempt_identity_is_bound_to_instance(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)
    with Session(engine) as session, session.begin():
        session.add(
            RuntimeInstanceRecord(
                instance_id="instance-2",
                instance_kind="execution_worker",
                owner_kind="paper_probe",
                owner_id="owner-1",
                owner_revision="owner-revision-1",
                management_mode="supervisor",
                runtime_spec_revision_id=RUNTIME_SPEC.runtime_spec_revision_id,
                environment="paper",
                state_allocation_id="state-allocation-1",
                desired_state="running",
                lifecycle_status="starting",
                failure_latched=False,
                optimistic_version=8,
                created_at=NOW,
                retired_at=None,
            )
        )
    _update(engine, RuntimeLifecycleJobRecord, instance_id="instance-2")

    with pytest.raises(RuntimeInvalidTransition, match=r"^attempt_id_mismatch$"):
        repository.resolve_launch_authority_material(
            job.job_id,
            attempt_id,
            "supervisor-1",
            job.lease_generation,
        )


def test_launch_authority_attempt_identity_is_bound_to_job(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)
    _update(engine, RuntimeLifecycleJobRecord, job_id="job-renamed")

    with pytest.raises(RuntimeInvalidTransition, match=r"^attempt_id_mismatch$"):
        repository.resolve_launch_authority_material(
            "job-renamed",
            attempt_id,
            "supervisor-1",
            job.lease_generation,
        )


def test_launch_authority_rejects_terminal_and_stop_jobs(
    engine: Engine,
    repository: SqlRuntimeRepository,
) -> None:
    _seed_launch_authority(engine)
    job, attempt_id = _claim_start(repository)
    _update(engine, RuntimeLifecycleJobRecord, status="succeeded")

    with pytest.raises(RuntimeInvalidTransition, match=r"^job_not_leased$"):
        repository.resolve_launch_authority_material(
            job.job_id,
            attempt_id,
            "supervisor-1",
            job.lease_generation,
        )

    _update(engine, RuntimeLifecycleJobRecord, status="claimed", requested_action="stop")
    with pytest.raises(
        RuntimeInvalidTransition,
        match=r"^attempt_requires_start_or_retry_job$",
    ):
        repository.resolve_launch_authority_material(
            job.job_id,
            attempt_id,
            "supervisor-1",
            job.lease_generation,
        )
