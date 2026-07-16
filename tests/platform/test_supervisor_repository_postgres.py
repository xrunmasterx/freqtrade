import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from freqtrade.markets.catalog import ProductType
from freqtrade.markets.instrument import MarketType
from freqtrade.platform import runtime_service
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.runtime_domain import RuntimeLifecycleCommand, RuntimeOwnerRef
from freqtrade.platform.runtime_models import (
    RuntimeAttemptRecord,
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
    RuntimeLifecycleJobRecord,
)
from freqtrade.platform.runtime_repository import SqlRuntimeRepository
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
from freqtrade.platform.template_repository import CommittedTemplatePublication


ADMIN_URL_ENV = "PLATFORM_TEST_SUPERVISOR_ADMIN_POSTGRES_URL"
SUPERVISOR_URL_ENV = "PLATFORM_TEST_SUPERVISOR_POSTGRES_URL"
DATABASE_SENTINEL_ENV = "PLATFORM_TEST_SUPERVISOR_DATABASE_SENTINEL"
POSTGRES_SKIP_REASON = (
    f"{ADMIN_URL_ENV}, {SUPERVISOR_URL_ENV}, and {DATABASE_SENTINEL_ENV} are required "
    "for the restricted Supervisor PostgreSQL lifecycle test"
)
TEST_NOW = datetime(2000, 1, 1, tzinfo=UTC)
DATABASE_SENTINEL_PATTERN = re.compile(
    r"^root-safety-ephemeral-platform-ci:[0-9a-f]{32}$"
)
VALID_TEST_DATABASE_SENTINEL = f"root-safety-ephemeral-platform-ci:{'a' * 32}"
AUTHORITY_TABLES = (
    "alembic_version",
    "adapter_template_revisions",
    "runtime_spec_revisions",
    "state_allocations",
    "secret_references",
    "secret_version_metadata",
)
NON_SELECT_TABLE_PRIVILEGES = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
COLUMN_WRITE_PRIVILEGES = ("INSERT", "UPDATE", "REFERENCES")
EXPECTED_AUTHORITY_COLUMN_WRITES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("state_allocations", "ready_at", "UPDATE"),
        ("state_allocations", "status", "UPDATE"),
    }
)


class _RedactedPostgresUrl(str):
    def __repr__(self) -> str:
        return "'<redacted restricted PostgreSQL test URL>'"


@dataclass(frozen=True)
class _PostgresUrls:
    admin: _RedactedPostgresUrl
    supervisor: _RedactedPostgresUrl
    database_sentinel: str


@dataclass(frozen=True)
class _FixtureMaterial:
    catalog_revision_id: str
    template_revision_id: str
    state_allocation_id: str
    secret_reference_id: str
    secret_version_id: str
    runtime_spec: RuntimeSpecRevision
    instance_id: str
    owner_id: str
    owner_revision: str
    idempotency_key: str
    template_publication: CommittedTemplatePublication


class _UniqueIds:
    def __init__(self, namespace: str) -> None:
        self._namespace = namespace
        self._count = 0

    def __call__(self, prefix: str) -> str:
        self._count += 1
        return f"{prefix}-{self._namespace}-{self._count}"


def _require_restricted_test_urls(
    admin_url: str,
    supervisor_url: str,
    database_sentinel: str,
) -> _PostgresUrls:
    admin = make_url(admin_url)
    supervisor = make_url(supervisor_url)
    for parsed in (admin, supervisor):
        if parsed.drivername != "postgresql+psycopg":
            raise RuntimeError("restricted Supervisor test requires psycopg PostgreSQL URLs")
        if parsed.host != "127.0.0.1":
            raise RuntimeError("restricted Supervisor test requires the CI loopback host")
        if parsed.port != 55432:
            raise RuntimeError("restricted Supervisor test requires the CI PostgreSQL port")
        if parsed.database != "platform":
            raise RuntimeError("restricted Supervisor test requires the platform CI database")
        if parsed.query:
            raise RuntimeError("restricted Supervisor test forbids database URL parameters")
    if admin.username != "postgres":
        raise RuntimeError("restricted Supervisor admin URL must use postgres")
    if supervisor.username != "platform_supervisor":
        raise RuntimeError("restricted Supervisor URL must use platform_supervisor")
    if DATABASE_SENTINEL_PATTERN.fullmatch(database_sentinel) is None:
        raise RuntimeError("restricted Supervisor database sentinel is invalid")
    return _PostgresUrls(
        admin=_RedactedPostgresUrl(admin.render_as_string(hide_password=False)),
        supervisor=_RedactedPostgresUrl(supervisor.render_as_string(hide_password=False)),
        database_sentinel=database_sentinel,
    )


def _load_restricted_test_urls(environ: Mapping[str, str]) -> _PostgresUrls | None:
    admin_url = environ.get(ADMIN_URL_ENV)
    supervisor_url = environ.get(SUPERVISOR_URL_ENV)
    database_sentinel = environ.get(DATABASE_SENTINEL_ENV)
    values = (admin_url, supervisor_url, database_sentinel)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise RuntimeError("restricted Supervisor test environment is incomplete")
    assert admin_url is not None
    assert supervisor_url is not None
    assert database_sentinel is not None
    return _require_restricted_test_urls(admin_url, supervisor_url, database_sentinel)


@pytest.fixture
def restricted_postgres_urls() -> _PostgresUrls:
    urls = _load_restricted_test_urls(os.environ)
    if urls is None:
        pytest.skip(POSTGRES_SKIP_REASON)
    return urls


def test_restricted_test_urls_bind_distinct_roles_to_one_local_database() -> None:
    urls = _require_restricted_test_urls(
        "postgresql+psycopg://postgres:admin@127.0.0.1:55432/platform",
        "postgresql+psycopg://platform_supervisor:restricted@127.0.0.1:55432/platform",
        VALID_TEST_DATABASE_SENTINEL,
    )

    assert repr(urls.admin) == "'<redacted restricted PostgreSQL test URL>'"
    assert repr(urls.supervisor) == "'<redacted restricted PostgreSQL test URL>'"
    assert urls.database_sentinel == VALID_TEST_DATABASE_SENTINEL


@pytest.mark.parametrize(
    ("admin_url", "supervisor_url"),
    (
        (
            "postgresql://postgres:x@127.0.0.1:55432/platform",
            "postgresql://platform_supervisor:y@127.0.0.1:55432/platform",
        ),
        (
            "postgresql+psycopg://postgres:x@localhost:55432/platform",
            "postgresql+psycopg://platform_supervisor:y@localhost:55432/platform",
        ),
        (
            "postgresql+psycopg://postgres:x@127.0.0.1:5432/platform",
            "postgresql+psycopg://platform_supervisor:y@127.0.0.1:5432/platform",
        ),
        (
            "postgresql+psycopg://platform_operator:x@127.0.0.1:55432/platform",
            "postgresql+psycopg://platform_supervisor:y@127.0.0.1:55432/platform",
        ),
        (
            "postgresql+psycopg://postgres:x@127.0.0.1:55432/platform?sslmode=disable",
            "postgresql+psycopg://platform_supervisor:y@127.0.0.1:55432/platform",
        ),
        (
            "postgresql+psycopg://platform_supervisor:x@127.0.0.1:55432/platform",
            "postgresql+psycopg://platform_supervisor:y@127.0.0.1:55432/platform",
        ),
        (
            "postgresql+psycopg://postgres:x@127.0.0.1:55432/platform",
            "postgresql+psycopg://postgres:y@127.0.0.1:55432/platform",
        ),
        (
            "postgresql+psycopg://postgres:x@127.0.0.1:55432/platform",
            "postgresql+psycopg://platform_supervisor:y@127.0.0.1:55432/other",
        ),
    ),
)
def test_restricted_test_urls_reject_unsafe_role_or_database_bindings(
    admin_url: str,
    supervisor_url: str,
) -> None:
    with pytest.raises(RuntimeError):
        _require_restricted_test_urls(
            admin_url,
            supervisor_url,
            VALID_TEST_DATABASE_SENTINEL,
        )


@pytest.mark.parametrize(
    "database_sentinel",
    (
        "root-safety-ephemeral-platform-ci",
        f"root-safety-ephemeral-platform-ci:{'A' * 32}",
        f"root-safety-ephemeral-platform-ci:{'a' * 31}",
        f"{VALID_TEST_DATABASE_SENTINEL},{VALID_TEST_DATABASE_SENTINEL}",
    ),
)
def test_restricted_test_urls_reject_invalid_or_multiple_sentinels(
    database_sentinel: str,
) -> None:
    with pytest.raises(RuntimeError):
        _require_restricted_test_urls(
            "postgresql+psycopg://postgres:x@127.0.0.1:55432/platform",
            "postgresql+psycopg://platform_supervisor:y@127.0.0.1:55432/platform",
            database_sentinel,
        )


def test_restricted_test_environment_skips_only_when_all_values_are_absent() -> None:
    assert _load_restricted_test_urls({}) is None
    complete_environment = {
        ADMIN_URL_ENV: "postgresql+psycopg://postgres:x@127.0.0.1:55432/platform",
        SUPERVISOR_URL_ENV: (
            "postgresql+psycopg://platform_supervisor:y@127.0.0.1:55432/platform"
        ),
        DATABASE_SENTINEL_ENV: VALID_TEST_DATABASE_SENTINEL,
    }
    assert _load_restricted_test_urls(complete_environment) is not None
    for missing_name in complete_environment:
        incomplete_environment = dict(complete_environment)
        incomplete_environment.pop(missing_name)
        with pytest.raises(RuntimeError, match=r"environment is incomplete$"):
            _load_restricted_test_urls(incomplete_environment)


def _fixture_material() -> _FixtureMaterial:
    token = uuid4().hex[:12]
    instance_id = f"pg-lifecycle-instance-{token}"
    owner_id = f"pg-lifecycle-owner-{token}"
    owner_revision = f"pg-lifecycle-owner-revision-{token}"
    catalog_revision_id = f"pg-lifecycle-catalog-{token}"
    state_allocation_id = f"pg-lifecycle-state-{token}"
    secret_reference_id = f"pg-lifecycle-secret-{token}"
    secret_version_id = f"pg-lifecycle-secret-version-{token}"
    template = AdapterTemplate(
        template_id=f"pg-lifecycle-template-{token}",
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
    canonical_template = json.dumps(
        {"schema_version": 1, **template.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"
    template_publication = CommittedTemplatePublication(
        template=template,
        canonical_payload=canonical_template,
        payload_digest=hashlib.sha256(canonical_template.encode()).hexdigest(),
        source_commit="1" * 40,
        root_commit="1" * 40,
        backend_commit="2" * 40,
        frontend_commit="3" * 40,
        strategies_commit="4" * 40,
    )
    template_revision_id = f"template-{template_publication.payload_digest}"
    runtime_spec = RuntimeSpecRevision.from_payload(
        RuntimeSpecPayload(
            owner_ref=RuntimeOwnerRef(
                owner_kind="paper_probe",
                owner_id=owner_id,
                owner_revision=owner_revision,
            ),
            instance_kind="execution_worker",
            catalog_revision_id=catalog_revision_id,
            market_scope=RuntimeMarketScope(
                market_id=MarketType.DIGITAL_ASSET,
                product_ids=(ProductType.SPOT,),
            ),
            environment="paper",
            adapter_template_revision_id=template_revision_id,
            template_digest=template_publication.payload_digest,
            image_policy_id="reviewed-image-v1",
            command_policy_id="fixed-command-v1",
            mount_policy_ids=("runtime-mounts-v1",),
            network_policy_id="private-network-v1",
            health_profile_id="api-ping-v1",
            resource_profile_id="paper-small-v1",
            state_layout_id="fixture-layout-1",
            state_allocation_id=state_allocation_id,
            secret_reference_ids=(secret_reference_id,),
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
    return _FixtureMaterial(
        catalog_revision_id=catalog_revision_id,
        template_revision_id=template_revision_id,
        state_allocation_id=state_allocation_id,
        secret_reference_id=secret_reference_id,
        secret_version_id=secret_version_id,
        runtime_spec=runtime_spec,
        instance_id=instance_id,
        owner_id=owner_id,
        owner_revision=owner_revision,
        idempotency_key=f"pg-lifecycle-start-{token}",
        template_publication=template_publication,
    )


def _seed_fixture(engine: Engine, material: _FixtureMaterial) -> str:
    publication = material.template_publication
    with Session(engine) as session, session.begin():
        session.add_all(
            (
                CatalogRevisionRecord(
                    revision_id=material.catalog_revision_id,
                    payload={"schema_version": 1},
                    created_at=TEST_NOW,
                ),
                AdapterTemplateRevisionRecord(
                    adapter_template_revision_id=material.template_revision_id,
                    template_id=publication.template.template_id,
                    semantic_version=publication.template.semantic_version,
                    canonical_payload=publication.canonical_payload,
                    payload_digest=publication.payload_digest,
                    source_commit=publication.source_commit,
                    root_commit=publication.root_commit,
                    backend_commit=publication.backend_commit,
                    frontend_commit=publication.frontend_commit,
                    strategies_commit=publication.strategies_commit,
                    status="active",
                    published_by="platform-test-admin",
                    published_at=TEST_NOW,
                    deprecated_at=None,
                    revoked_at=None,
                ),
                StateAllocationRecord(
                    state_allocation_id=material.state_allocation_id,
                    instance_id=material.instance_id,
                    layout_id="fixture-layout-1",
                    provider_id="managed-local-v1",
                    relative_path=(
                        f"ft_userdata/runtime/instances/{material.instance_id}"
                    ),
                    kind="fresh",
                    status="reserved",
                    generation=1,
                    restore_source_bundle_id=None,
                    created_at=TEST_NOW,
                    ready_at=None,
                    retired_at=None,
                ),
                SecretReferenceRecord(
                    secret_reference_id=material.secret_reference_id,
                    provider_id="local-file-v1",
                    secret_class="exchange_credentials",
                    logical_name=f"paper-exchange-{material.instance_id}",
                    owner_kind="paper_probe",
                    owner_id=material.owner_id,
                    owner_revision=material.owner_revision,
                    status="active",
                    created_at=TEST_NOW,
                    retired_at=None,
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                RuntimeSpecRevisionRecord(
                    runtime_spec_revision_id=material.runtime_spec.runtime_spec_revision_id,
                    owner_kind="paper_probe",
                    owner_id=material.owner_id,
                    owner_revision=material.owner_revision,
                    instance_kind="execution_worker",
                    catalog_revision_id=material.catalog_revision_id,
                    environment="paper",
                    adapter_template_revision_id=material.template_revision_id,
                    state_allocation_id=material.state_allocation_id,
                    canonical_payload=material.runtime_spec.canonical_payload,
                    payload_digest=material.runtime_spec.payload_digest,
                    created_at=TEST_NOW,
                ),
                SecretVersionMetadataRecord(
                    secret_reference_id=material.secret_reference_id,
                    version_id=material.secret_version_id,
                    status="active",
                    created_at=TEST_NOW,
                    activated_at=TEST_NOW,
                    retired_at=None,
                ),
            )
        )
        session.flush()
        session.add(
            RuntimeInstanceRecord(
                instance_id=material.instance_id,
                instance_kind="execution_worker",
                owner_kind="paper_probe",
                owner_id=material.owner_id,
                owner_revision=material.owner_revision,
                management_mode="supervisor",
                runtime_spec_revision_id=material.runtime_spec.runtime_spec_revision_id,
                environment="paper",
                state_allocation_id=material.state_allocation_id,
                desired_state="stopped",
                lifecycle_status="registered",
                failure_latched=False,
                optimistic_version=0,
                created_at=TEST_NOW,
                retired_at=None,
            )
        )

    repository = SqlRuntimeRepository(
        engine,
        clock=lambda: TEST_NOW,
        id_factory=_UniqueIds(f"admin-{material.instance_id}"),
    )
    job = repository.create_job(
        RuntimeLifecycleCommand(
            instance_id=material.instance_id,
            action="start",
            idempotency_key=material.idempotency_key,
            expected_instance_version=0,
        ),
        "operator_cli",
    )
    return job.job_id


def _cleanup_fixture(engine: Engine, material: _FixtureMaterial) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            delete(RuntimeAuditEventRecord).where(
                RuntimeAuditEventRecord.instance_id == material.instance_id
            )
        )
        session.execute(
            delete(RuntimeAttemptRecord).where(
                RuntimeAttemptRecord.instance_id == material.instance_id
            )
        )
        session.execute(
            delete(RuntimeLifecycleJobRecord).where(
                RuntimeLifecycleJobRecord.instance_id == material.instance_id
            )
        )
        session.execute(
            delete(RuntimeInstanceRecord).where(
                RuntimeInstanceRecord.instance_id == material.instance_id
            )
        )
        session.execute(
            delete(RuntimeSpecRevisionRecord).where(
                RuntimeSpecRevisionRecord.runtime_spec_revision_id
                == material.runtime_spec.runtime_spec_revision_id
            )
        )
        session.execute(
            delete(SecretVersionMetadataRecord).where(
                SecretVersionMetadataRecord.secret_reference_id
                == material.secret_reference_id
            )
        )
        session.execute(
            delete(SecretReferenceRecord).where(
                SecretReferenceRecord.secret_reference_id == material.secret_reference_id
            )
        )
        session.execute(
            delete(StateAllocationRecord).where(
                StateAllocationRecord.state_allocation_id == material.state_allocation_id
            )
        )
        session.execute(
            delete(AdapterTemplateRevisionRecord).where(
                AdapterTemplateRevisionRecord.adapter_template_revision_id
                == material.template_revision_id
            )
        )
        session.execute(
            delete(CatalogRevisionRecord).where(
                CatalogRevisionRecord.revision_id == material.catalog_revision_id
            )
        )


def _assert_root_safety_database_is_empty(engine: Engine, database_sentinel: str) -> None:
    with engine.connect() as connection:
        database_identity = connection.execute(
            text(
                "SELECT current_user, current_database(), "
                "shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname = current_database()"
            )
        ).one()
        if tuple(database_identity) != (
            "postgres",
            "platform",
            database_sentinel,
        ):
            raise RuntimeError("restricted Supervisor test database sentinel differs")
        if connection.scalar(text("SELECT count(*) FROM runtime_lifecycle_jobs")) != 0:
            raise RuntimeError("restricted Supervisor test lifecycle queue is not empty")


def _assert_restricted_authority_reads(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == "platform_supervisor"
        for table_name in AUTHORITY_TABLES:
            qualified_name = f"public.{table_name}"
            assert connection.scalar(
                text("SELECT has_table_privilege(current_user, :name, 'SELECT')"),
                {"name": qualified_name},
            ) is True
            for privilege in NON_SELECT_TABLE_PRIVILEGES:
                assert connection.scalar(
                    text(
                        "SELECT has_table_privilege(current_user, :name, :privilege)"
                    ),
                    {"name": qualified_name, "privilege": privilege},
                ) is False
            for privilege in COLUMN_WRITE_PRIVILEGES:
                expected = any(
                    expected_table == table_name and expected_privilege == privilege
                    for expected_table, _, expected_privilege in (
                        EXPECTED_AUTHORITY_COLUMN_WRITES
                    )
                )
                assert connection.scalar(
                    text(
                        "SELECT has_any_column_privilege("
                        "current_user, :name, :privilege)"
                    ),
                    {"name": qualified_name, "privilege": privilege},
                ) is expected

        actual_column_writes = {
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT table_name, column_name, privilege_type "
                    "FROM information_schema.column_privileges "
                    "WHERE table_schema = 'public' AND grantee = current_user "
                    "AND privilege_type IN ('INSERT', 'UPDATE', 'REFERENCES')"
                )
            )
            if row.table_name in AUTHORITY_TABLES
        }
        assert actual_column_writes == EXPECTED_AUTHORITY_COLUMN_WRITES


def test_platform_supervisor_runs_repository_lifecycle_with_bounded_authority(
    restricted_postgres_urls: _PostgresUrls,
) -> None:
    material = _fixture_material()
    admin_engine = create_engine(restricted_postgres_urls.admin)
    supervisor_engine = create_engine(restricted_postgres_urls.supervisor)
    try:
        _assert_root_safety_database_is_empty(
            admin_engine,
            restricted_postgres_urls.database_sentinel,
        )
        job_id = _seed_fixture(admin_engine, material)
        _assert_restricted_authority_reads(supervisor_engine)
        repository = SqlRuntimeRepository(
            supervisor_engine,
            clock=lambda: TEST_NOW,
            id_factory=_UniqueIds(f"supervisor-{material.instance_id}"),
        )

        claimed = repository.claim_next_job("supervisor-lifecycle-test", lease_seconds=30)
        assert claimed is not None
        assert claimed.job_id == job_id
        renewed = repository.renew_lease(
            claimed.job_id,
            "supervisor-lifecycle-test",
            claimed.lease_generation,
            lease_seconds=60,
        )
        lease = {
            "lease_owner": "supervisor-lifecycle-test",
            "lease_generation": renewed.lease_generation,
        }
        first_attempt_id = repository.prepare_attempt_id(renewed.job_id, **lease)
        assert repository.prepare_attempt_id(renewed.job_id, **lease) == first_attempt_id

        provisioning = repository.begin_state_provisioning(renewed.job_id, **lease)
        assert provisioning.state_allocation_id == material.state_allocation_id
        assert provisioning.status == "provisioning"
        ready = repository.complete_state_provisioning(
            renewed.job_id,
            provisioning.state_allocation_id,
            provisioning.generation,
            **lease,
        )
        assert ready.status == "ready"
        assert ready.ready_at == TEST_NOW

        authority = repository.resolve_launch_authority_material(
            renewed.job_id,
            first_attempt_id,
            **lease,
        )
        assert authority.instance.instance_id == material.instance_id
        assert authority.runtime_spec.runtime_spec_revision_id == (
            material.runtime_spec.runtime_spec_revision_id
        )
        assert authority.adapter_template.adapter_template_revision_id == (
            material.template_revision_id
        )
        assert authority.state_allocation.state_allocation_id == material.state_allocation_id
        assert {
            reference.secret_reference_id: reference.active_version_id
            for reference in authority.secret_references
        } == {material.secret_reference_id: material.secret_version_id}

        resolved_material = runtime_service.ResolvedRuntimeMaterial(
            runtime_spec_revision_id=authority.runtime_spec.runtime_spec_revision_id,
            adapter_template_revision_id=(
                authority.adapter_template.adapter_template_revision_id
            ),
            state_allocation_id=authority.state_allocation.state_allocation_id,
            state_allocation_generation=authority.state_allocation.generation,
            resolved_secret_versions={
                reference.secret_reference_id: reference.active_version_id
                for reference in authority.secret_references
            },
            image_id=f"sha256:{'a' * 64}",
            root_commit=authority.adapter_template.root_commit,
            backend_commit=authority.adapter_template.backend_commit,
            frontend_commit=authority.adapter_template.frontend_commit,
            strategies_commit=authority.adapter_template.strategies_commit,
            project_identity=f"project-{material.instance_id}",
            container_identity=f"container-{material.instance_id}",
        )
        attempt = repository.begin_attempt(
            renewed.job_id,
            first_attempt_id,
            resolved_material,
            **lease,
        )
        assert attempt.status == "launching"
        active_authority = repository.revalidate_active_launch_authority_material(
            renewed.job_id,
            attempt.attempt_id,
            **lease,
        )
        assert active_authority.instance.instance_id == material.instance_id
        assert active_authority.instance.lifecycle_status == "starting"
        assert active_authority.runtime_spec == authority.runtime_spec
        assert active_authority.adapter_template == authority.adapter_template
        assert active_authority.state_allocation == authority.state_allocation
        assert active_authority.secret_references == authority.secret_references

        reservation = repository.reserve_health_probe(
            renewed.job_id,
            attempt.attempt_id,
            "api-ping-v1",
            "8" * 64,
            TEST_NOW + timedelta(seconds=20),
            TEST_NOW,
            **lease,
        )
        observed = repository.record_health_observation(
            renewed.job_id,
            attempt.attempt_id,
            "health_probe_healthy",
            reservation.attempts,
            None,
            **lease,
        )
        assert observed.health_result == "health_probe_healthy"

        healthy = repository.record_healthy(
            renewed.job_id,
            attempt.attempt_id,
            **lease,
        )
        assert healthy.status == "healthy"
        # record_healthy is the successful terminal transition and atomically
        # completes the leased start job; complete_job must not be called again.
        completed_job = next(
            job for job in repository.list_jobs(material.instance_id) if job.job_id == job_id
        )
        assert completed_job.status == "succeeded"
        assert completed_job.lease_owner is None
        assert completed_job.lease_expires_at is None
        assert repository.get_instance(material.instance_id).lifecycle_status == "healthy"
    finally:
        try:
            _cleanup_fixture(admin_engine, material)
        finally:
            supervisor_engine.dispose()
            admin_engine.dispose()
