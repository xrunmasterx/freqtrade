import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, func, select, update
from sqlalchemy.orm import Session

from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_models import (
    RuntimeAccessRequestRecord,
    RuntimeAttemptRecord,
    RuntimeAuditEventRecord,
    RuntimeEndpointRecord,
    RuntimeInstanceRecord,
    RuntimeLifecycleJobRecord,
)
from freqtrade.platform.runtime_registration import (
    PAPER_PROBE_AUDIT_EVENT_ID,
    PAPER_PROBE_INSTANCE_ID,
    PAPER_PROBE_SECRET_REFERENCE_IDS,
    PAPER_PROBE_STATE_ALLOCATION_ID,
    EnsurePaperProbeRegistrationRequest,
)
from freqtrade.platform.runtime_registration_repository import (
    PaperProbeRegistrationConflict,
    SqlPaperProbeRegistrationRepository,
)
from freqtrade.platform.template_domain import AdapterTemplate
from freqtrade.platform.template_models import (
    RuntimeSpecRevisionRecord,
    SecretReferenceRecord,
    SecretVersionMetadataRecord,
    StateAllocationRecord,
)
from freqtrade.platform.template_repository import (
    CommittedTemplatePublication,
    SqlTemplateRepository,
)


NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)
ROOT_COMMIT = "1" * 40
BACKEND_COMMIT = "2" * 40
FRONTEND_COMMIT = "3" * 40
STRATEGIES_COMMIT = "4" * 40


def _template() -> AdapterTemplate:
    return AdapterTemplate(
        template_id="freqtrade-paper-probe-v1",
        semantic_version="1.0.0",
        allowed_instance_kinds=("freqtrade",),
        allowed_owner_kinds=("paper_probe",),
        allowed_environments=("paper",),
        image_policy_id="freqtrade-reviewed-image-v1",
        command_policy_id="freqtrade-spot-paper-v1",
        mount_policy_ids=(
            "runtime-config-ro-v1",
            "safety-policy-ro-v1",
            "strategy-ro-v1",
            "managed-state-rw-v1",
            "api-secrets-ro-v1",
        ),
        network_policy_id="isolated-public-market-data-v1",
        health_profile_id="freqtrade-ping-v1",
        resource_profile_id="freqtrade-small-v1",
        secret_classes=("api_password", "jwt_secret", "ws_token"),
        state_layout_id="freqtrade-state-v1",
    )


def _publication() -> CommittedTemplatePublication:
    template = _template()
    canonical_payload = (
        json.dumps(
            {"schema_version": 1, **template.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )
    return CommittedTemplatePublication(
        template=template,
        canonical_payload=canonical_payload,
        payload_digest=hashlib.sha256(canonical_payload.encode()).hexdigest(),
        source_commit=ROOT_COMMIT,
        root_commit=ROOT_COMMIT,
        backend_commit=BACKEND_COMMIT,
        frontend_commit=FRONTEND_COMMIT,
        strategies_commit=STRATEGIES_COMMIT,
    )


def _request(template_revision_id: str) -> EnsurePaperProbeRegistrationRequest:
    return EnsurePaperProbeRegistrationRequest(
        adapter_template_revision_id=template_revision_id,
        component_commits={
            "root_commit": ROOT_COMMIT,
            "backend_commit": BACKEND_COMMIT,
            "frontend_commit": FRONTEND_COMMIT,
            "strategies_commit": STRATEGIES_COMMIT,
        },
        config_blob_digest="b" * 64,
        strategy_digest="c" * 64,
        safety_policy_digest="d" * 64,
        strategy_class_name="SampleStrategy",
        closed_policy_snapshot={
            "image_policy_ids": ["freqtrade-reviewed-image-v1"],
            "command_policy_ids": ["freqtrade-spot-paper-v1"],
            "mount_policy_ids": [
                "runtime-config-ro-v1",
                "safety-policy-ro-v1",
                "strategy-ro-v1",
                "managed-state-rw-v1",
                "api-secrets-ro-v1",
            ],
            "network_policy_ids": ["isolated-public-market-data-v1"],
            "health_profile_ids": ["freqtrade-ping-v1"],
            "resource_profile_ids": ["freqtrade-small-v1"],
            "state_layout_ids": ["freqtrade-state-v1"],
            "source_commit": ROOT_COMMIT,
        },
    )


@pytest.fixture
def engine() -> Engine:
    value = create_engine("sqlite+pysqlite:///:memory:")
    PlatformBase.metadata.create_all(value)
    try:
        yield value
    finally:
        value.dispose()


def _published_request(engine: Engine) -> EnsurePaperProbeRegistrationRequest:
    revision = SqlTemplateRepository(engine).publish_template(
        _publication(),
        "platform-admin",
        NOW,
    )
    return _request(revision.revision_id)


def _count(session: Session, record_type: type[PlatformBase]) -> int:
    return session.scalar(select(func.count()).select_from(record_type)) or 0


def test_registration_is_atomic_replay_safe_and_has_no_runtime_side_effects(
    engine: Engine,
) -> None:
    request = _published_request(engine)
    repository = SqlPaperProbeRegistrationRepository(engine)

    first = repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
    replay = repository.ensure_paper_probe_registration(request, "other_operator", NOW)

    assert replay == first
    assert first.instance_id == PAPER_PROBE_INSTANCE_ID
    assert first.adapter_template_revision_id == request.adapter_template_revision_id
    assert first.catalog_revision_id == "builtin-market-catalog-v2"
    assert first.state_allocation_id == PAPER_PROBE_STATE_ALLOCATION_ID
    assert first.secret_reference_ids == PAPER_PROBE_SECRET_REFERENCE_IDS
    assert first.desired_state == "stopped"
    assert first.lifecycle_status == "registered"
    with Session(engine) as session:
        assert session.get(CatalogRevisionRecord, "builtin-market-catalog-v2") is not None
        assert session.get(StateAllocationRecord, PAPER_PROBE_STATE_ALLOCATION_ID) is not None
        assert _count(session, SecretReferenceRecord) == 3
        assert _count(session, RuntimeSpecRevisionRecord) == 1
        assert _count(session, RuntimeInstanceRecord) == 1
        assert session.get(RuntimeAuditEventRecord, PAPER_PROBE_AUDIT_EVENT_ID) is not None
        assert (
            session.scalar(
                select(func.count()).where(RuntimeAuditEventRecord.action == "register_paper_probe")
            )
            == 1
        )
        assert _count(session, SecretVersionMetadataRecord) == 0
        assert _count(session, RuntimeLifecycleJobRecord) == 0
        assert _count(session, RuntimeAttemptRecord) == 0
        assert _count(session, RuntimeEndpointRecord) == 0
        assert _count(session, RuntimeAccessRequestRecord) == 0


def test_registration_status_is_historical_and_does_not_require_active_template(
    engine: Engine,
) -> None:
    request = _published_request(engine)
    repository = SqlPaperProbeRegistrationRepository(engine)
    expected = repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
    SqlTemplateRepository(engine).deprecate_template(
        request.adapter_template_revision_id,
        "platform-admin",
        NOW,
    )

    assert repository.registration_status(PAPER_PROBE_INSTANCE_ID) == expected


def test_registration_rejects_non_exact_catalog_and_rolls_back_all_new_rows(
    engine: Engine,
) -> None:
    request = _published_request(engine)
    from freqtrade.markets.default_catalog import default_catalog_snapshot

    payload = default_catalog_snapshot().model_dump(mode="json")
    payload["unexpected"] = "coercible-but-not-exact"
    with Session(engine) as session, session.begin():
        session.add(
            CatalogRevisionRecord(
                revision_id="builtin-market-catalog-v2",
                payload=payload,
                created_at=NOW,
            )
        )

    with pytest.raises(
        PaperProbeRegistrationConflict,
        match=r"^paper_probe_registration_conflict$",
    ):
        SqlPaperProbeRegistrationRepository(engine).ensure_paper_probe_registration(
            request,
            "operator_cli",
            NOW,
        )

    with Session(engine) as session:
        assert _count(session, StateAllocationRecord) == 0
        assert _count(session, SecretReferenceRecord) == 0
        assert _count(session, RuntimeSpecRevisionRecord) == 0
        assert _count(session, RuntimeInstanceRecord) == 0
        assert (
            session.scalar(
                select(func.count()).where(RuntimeAuditEventRecord.action == "register_paper_probe")
            )
            == 0
        )


def test_registration_rejects_catalog_json_with_equal_but_different_raw_types(
    engine: Engine,
) -> None:
    request = _published_request(engine)
    from freqtrade.markets.default_catalog import default_catalog_snapshot

    payload = default_catalog_snapshot().model_dump(mode="json")
    payload["catalog"]["schema_version"] = 1.0
    with Session(engine) as session, session.begin():
        session.add(
            CatalogRevisionRecord(
                revision_id="builtin-market-catalog-v2",
                payload=payload,
                created_at=NOW,
            )
        )

    with pytest.raises(PaperProbeRegistrationConflict):
        SqlPaperProbeRegistrationRepository(engine).ensure_paper_probe_registration(
            request,
            "operator_cli",
            NOW,
        )


def test_registration_compile_failure_rolls_back_catalog_and_reserved_inputs(
    engine: Engine,
) -> None:
    request = _published_request(engine)
    invalid = request.model_copy(
        update={
            "closed_policy_snapshot": request.closed_policy_snapshot.model_copy(
                update={"image_policy_ids": frozenset({"different-image-policy"})}
            )
        }
    )

    with pytest.raises(PaperProbeRegistrationConflict):
        SqlPaperProbeRegistrationRepository(engine).ensure_paper_probe_registration(
            invalid,
            "operator_cli",
            NOW,
        )

    with Session(engine) as session:
        assert session.get(CatalogRevisionRecord, "builtin-market-catalog-v2") is None
        assert _count(session, StateAllocationRecord) == 0
        assert _count(session, SecretReferenceRecord) == 0
        assert _count(session, RuntimeSpecRevisionRecord) == 0
        assert _count(session, RuntimeInstanceRecord) == 0


@pytest.mark.parametrize("corruption", ["missing_reference", "ready_state", "extra_reference"])
def test_registration_replay_rejects_partial_or_conflicting_reserved_inputs(
    engine: Engine,
    corruption: str,
) -> None:
    request = _published_request(engine)
    repository = SqlPaperProbeRegistrationRepository(engine)
    expected = repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
    with Session(engine) as session, session.begin():
        if corruption == "missing_reference":
            session.delete(session.get(SecretReferenceRecord, PAPER_PROBE_SECRET_REFERENCE_IDS[0]))
        elif corruption == "ready_state":
            session.execute(
                update(StateAllocationRecord)
                .where(StateAllocationRecord.state_allocation_id == PAPER_PROBE_STATE_ALLOCATION_ID)
                .values(status="ready", ready_at=NOW)
            )
        else:
            session.add(
                SecretReferenceRecord(
                    secret_reference_id="secret-phase2-spot-paper-probe-extra-v1",
                    provider_id="local-file-v1",
                    secret_class="extra_secret",
                    logical_name="extra_secret",
                    owner_kind="paper_probe",
                    owner_id=PAPER_PROBE_INSTANCE_ID,
                    owner_revision="phase2-spot-paper-probe-v1",
                    status="active",
                    created_at=NOW,
                    retired_at=None,
                )
            )

    with pytest.raises(PaperProbeRegistrationConflict):
        repository.ensure_paper_probe_registration(request, "operator_cli", NOW)

    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count()).where(RuntimeAuditEventRecord.action == "register_paper_probe")
            )
            == 1
        )
        assert session.get(RuntimeInstanceRecord, PAPER_PROBE_INSTANCE_ID) is not None
        assert session.get(RuntimeSpecRevisionRecord, expected.runtime_spec_revision_id) is not None
        if corruption == "missing_reference":
            assert _count(session, SecretReferenceRecord) == 2
        elif corruption == "ready_state":
            allocation = session.get(StateAllocationRecord, PAPER_PROBE_STATE_ALLOCATION_ID)
            assert allocation.status == "ready"
        else:
            assert _count(session, SecretReferenceRecord) == 4


def test_registration_replay_rejects_conflicting_artifact_evidence_without_second_audit(
    engine: Engine,
) -> None:
    request = _published_request(engine)
    repository = SqlPaperProbeRegistrationRepository(engine)
    repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
    conflicting = request.model_copy(update={"config_blob_digest": "e" * 64})

    with pytest.raises(PaperProbeRegistrationConflict):
        repository.ensure_paper_probe_registration(conflicting, "operator_cli", NOW)

    with Session(engine) as session:
        assert _count(session, RuntimeSpecRevisionRecord) == 1
        assert (
            session.scalar(
                select(func.count()).where(RuntimeAuditEventRecord.action == "register_paper_probe")
            )
            == 1
        )


def test_registration_replay_rejects_corrupt_audit_provenance() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PlatformBase.metadata.create_all(engine)
    try:
        request = _published_request(engine)
        repository = SqlPaperProbeRegistrationRepository(engine)
        repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
        with Session(engine) as session, session.begin():
            audit = session.get(RuntimeAuditEventRecord, PAPER_PROBE_AUDIT_EVENT_ID)
            audit.provenance = {**audit.provenance, "root_commit": "9" * 40}

        with pytest.raises(PaperProbeRegistrationConflict):
            repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
    finally:
        engine.dispose()


def test_registration_audit_contains_only_exact_identifiers_digests_and_commits(
    engine: Engine,
) -> None:
    request = _published_request(engine)
    result = SqlPaperProbeRegistrationRepository(engine).ensure_paper_probe_registration(
        request,
        "operator_cli",
        NOW,
    )

    with Session(engine) as session:
        audit = session.get(RuntimeAuditEventRecord, PAPER_PROBE_AUDIT_EVENT_ID)
        assert audit.provenance == {
            "source": "runtime_registration_repository",
            "catalog_revision_id": "builtin-market-catalog-v2",
            "runtime_spec_digest": result.runtime_spec_revision_id.removeprefix("runtime-spec-"),
            "template_digest": result.adapter_template_revision_id.removeprefix("template-"),
            "root_commit": ROOT_COMMIT,
            "backend_commit": BACKEND_COMMIT,
            "frontend_commit": FRONTEND_COMMIT,
            "strategies_commit": STRATEGIES_COMMIT,
            "config_blob_digest": "b" * 64,
            "strategy_digest": "c" * 64,
            "safety_policy_digest": "d" * 64,
        }


def test_registration_replay_rejects_corrupt_audit_actor(engine: Engine) -> None:
    request = _published_request(engine)
    repository = SqlPaperProbeRegistrationRepository(engine)
    repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
    with Session(engine) as session, session.begin():
        audit = session.get(RuntimeAuditEventRecord, PAPER_PROBE_AUDIT_EVENT_ID)
        audit.actor_type = "invalid actor"

    with pytest.raises(PaperProbeRegistrationConflict):
        repository.ensure_paper_probe_registration(request, "operator_cli", NOW)


@pytest.mark.parametrize("missing", ["instance", "audit"])
def test_registration_replay_does_not_heal_partial_registration(
    engine: Engine,
    missing: str,
) -> None:
    request = _published_request(engine)
    repository = SqlPaperProbeRegistrationRepository(engine)
    repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
    with Session(engine) as session, session.begin():
        if missing == "instance":
            session.delete(session.get(RuntimeInstanceRecord, PAPER_PROBE_INSTANCE_ID))
        else:
            session.delete(session.get(RuntimeAuditEventRecord, PAPER_PROBE_AUDIT_EVENT_ID))

    with pytest.raises(PaperProbeRegistrationConflict):
        repository.ensure_paper_probe_registration(request, "operator_cli", NOW)

    with Session(engine) as session:
        assert (session.get(RuntimeInstanceRecord, PAPER_PROBE_INSTANCE_ID) is None) is (
            missing == "instance"
        )
        assert (session.get(RuntimeAuditEventRecord, PAPER_PROBE_AUDIT_EVENT_ID) is None) is (
            missing == "audit"
        )
