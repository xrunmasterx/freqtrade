import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, func, insert, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_domain import (
    RuntimeAction,
    RuntimeAuditAction,
    RuntimeLifecycleCommand,
)
from freqtrade.platform.runtime_models import RuntimeAuditEventRecord
from freqtrade.platform.template_domain import AdapterTemplate, TemplateStatus
from freqtrade.platform.template_models import AdapterTemplateRevisionRecord
from freqtrade.platform.template_repository import (
    CommittedTemplatePublication,
    SqlTemplateRepository,
    TemplateConflict,
    TemplateDataError,
    TemplateInvalidTransition,
    TemplateNotFound,
)


NOW = datetime(2026, 7, 14, 9, 30, tzinfo=UTC)
ROOT_COMMIT = "1" * 40
BACKEND_COMMIT = "2" * 40
FRONTEND_COMMIT = "3" * 40
STRATEGIES_COMMIT = "4" * 40


class SequentialIds:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.count}"


def _template(**updates: object) -> AdapterTemplate:
    values: dict[str, object] = {
        "template_id": "freqtrade-spot",
        "semantic_version": "1.0.0",
        "allowed_instance_kinds": ("execution-worker",),
        "allowed_owner_kinds": ("migration_bot", "paper_probe"),
        "allowed_environments": ("paper", "live"),
        "image_policy_id": "freqtrade-stable",
        "command_policy_id": "freqtrade-trade",
        "mount_policy_ids": ("runtime-state",),
        "network_policy_id": "exchange-egress",
        "health_profile_id": "freqtrade-health",
        "resource_profile_id": "freqtrade-small",
        "secret_classes": ("exchange-api",),
        "state_layout_id": "freqtrade-userdata-v1",
    }
    values.update(updates)
    return AdapterTemplate.model_validate(values)


def _canonical_payload(template: AdapterTemplate) -> str:
    payload = {"schema_version": 1, **template.model_dump(mode="json")}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"


def _publication(
    *,
    template: AdapterTemplate | None = None,
    canonical_payload: str | None = None,
    payload_digest: str | None = None,
    **updates: object,
) -> CommittedTemplatePublication:
    selected_template = template or _template()
    selected_payload = canonical_payload or _canonical_payload(selected_template)
    values: dict[str, object] = {
        "template": selected_template,
        "canonical_payload": selected_payload,
        "payload_digest": payload_digest
        or hashlib.sha256(selected_payload.encode("utf-8")).hexdigest(),
        "source_commit": ROOT_COMMIT,
        "root_commit": ROOT_COMMIT,
        "backend_commit": BACKEND_COMMIT,
        "frontend_commit": FRONTEND_COMMIT,
        "strategies_commit": STRATEGIES_COMMIT,
    }
    values.update(updates)
    return CommittedTemplatePublication.model_validate(values)


def _record_values(
    committed: CommittedTemplatePublication,
    *,
    published_by: str = "competing-admin",
    **updates: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "adapter_template_revision_id": f"template-{committed.payload_digest}",
        "template_id": committed.template.template_id,
        "semantic_version": committed.template.semantic_version,
        "canonical_payload": committed.canonical_payload,
        "payload_digest": committed.payload_digest,
        "source_commit": committed.source_commit,
        "root_commit": committed.root_commit,
        "backend_commit": committed.backend_commit,
        "frontend_commit": committed.frontend_commit,
        "strategies_commit": committed.strategies_commit,
        "status": "active",
        "published_by": published_by,
        "published_at": NOW,
        "deprecated_at": None,
        "revoked_at": None,
    }
    values.update(updates)
    return values


@pytest.fixture
def engine() -> Engine:
    value = create_engine("sqlite+pysqlite:///:memory:")
    PlatformBase.metadata.create_all(value)
    try:
        yield value
    finally:
        value.dispose()


@pytest.fixture
def repository(engine: Engine) -> SqlTemplateRepository:
    return SqlTemplateRepository(engine, id_factory=SequentialIds())


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"payload_digest": "A" * 64}, "template_payload_digest_invalid"),
        ({"root_commit": "abc"}, "template_commit_invalid"),
        ({"source_commit": "5" * 40}, "template_source_commit_mismatch"),
    ],
)
def test_committed_publication_rejects_invalid_digest_and_commit_identity(
    update: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _publication(**update)


@pytest.mark.parametrize(
    "mutate_payload",
    [
        lambda payload: payload.rstrip("\n"),
        lambda payload: payload + "\n",
        lambda payload: payload.replace(":", ": ", 1),
        lambda payload: payload.replace(
            '"schema_version":1',
            '"schema_version":1,"schema_version":1',
        ),
        lambda payload: payload.replace('"schema_version":1', '"schema_version":NaN'),
    ],
)
def test_committed_publication_requires_strict_canonical_json(
    mutate_payload,
) -> None:
    payload = mutate_payload(_canonical_payload(_template()))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    with pytest.raises(ValidationError, match="template_payload_invalid"):
        _publication(canonical_payload=payload, payload_digest=digest)


def test_committed_publication_rejects_template_and_digest_mismatch_without_payload_leak() -> None:
    secret = "secret-material-must-not-leak"
    payload = _canonical_payload(_template()).replace("freqtrade-small", secret)

    with pytest.raises(ValidationError) as exc_info:
        _publication(
            canonical_payload=payload,
            payload_digest=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    assert "template_payload_template_mismatch" in str(exc_info.value)
    assert secret not in str(exc_info.value)

    canonical = _canonical_payload(_template())
    with pytest.raises(ValidationError, match="template_payload_digest_mismatch"):
        _publication(canonical_payload=canonical, payload_digest="0" * 64)


@pytest.mark.parametrize("corruption", ["canonical", "digest", "source_commit"])
def test_publish_revalidates_model_copy_before_opening_session(
    corruption: str,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed = _publication()
    if corruption == "canonical":
        payload = committed.canonical_payload.replace(":", ": ", 1)
        updates = {
            "canonical_payload": payload,
            "payload_digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
    elif corruption == "digest":
        updates = {"payload_digest": "0" * 64}
    else:
        updates = {"source_commit": "5" * 40}
    bypassed = committed.model_copy(update=updates)

    import freqtrade.platform.template_repository as repository_module

    def fail_if_session_opens(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("session opened before publication revalidation")

    monkeypatch.setattr(repository_module, "Session", fail_if_session_opens)
    repository = SqlTemplateRepository(engine, id_factory=SequentialIds())

    with pytest.raises(ValidationError):
        repository.publish_template(bypassed, "platform-admin", NOW)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AdapterTemplateRevisionRecord)) == 0
        assert session.scalar(select(func.count()).select_from(RuntimeAuditEventRecord)) == 0


def test_publish_round_trips_revision_and_atomic_audit(
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    published_at = datetime(
        2026,
        7,
        14,
        17,
        30,
        tzinfo=timezone(timedelta(hours=8)),
    )
    committed = _publication()

    view = repository.publish_template(committed, "platform-admin", published_at)

    assert view.revision_id == f"template-{committed.payload_digest}"
    assert view.template == committed.template
    assert view.payload_digest == committed.payload_digest
    assert view.status is TemplateStatus.ACTIVE
    assert view.published_at == NOW
    assert view.source_commit == view.root_commit == ROOT_COMMIT
    assert view.backend_commit == BACKEND_COMMIT
    assert view.frontend_commit == FRONTEND_COMMIT
    assert view.strategies_commit == STRATEGIES_COMMIT
    assert repository.get_template_revision(view.revision_id) == view

    with Session(engine) as session:
        record = session.get(AdapterTemplateRevisionRecord, view.revision_id)
        audit = session.scalar(select(RuntimeAuditEventRecord))
        assert record is not None
        assert record.canonical_payload == committed.canonical_payload
        assert audit is not None
        assert audit.adapter_template_revision_id == view.revision_id
        assert audit.action == "publish_template"
        assert audit.actor_type == "platform-admin"
        assert audit.result_code == "published"
        assert audit.previous_state is None
        assert audit.next_state == {"status": "active"}
        assert audit.provenance["source"] == "template_repository"
        assert audit.provenance["payload_digest"] == committed.payload_digest


def test_publish_is_idempotent_and_conflicting_digest_is_rejected(
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    committed = _publication()
    first = repository.publish_template(committed, "platform-admin", NOW)
    second = repository.publish_template(
        committed.model_copy(update={"backend_commit": "9" * 40}),
        "other-admin",
        NOW + timedelta(hours=1),
    )

    assert second == first
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RuntimeAuditEventRecord)) == 1

    changed_template = _template(resource_profile_id="freqtrade-large")
    with pytest.raises(TemplateConflict, match=r"^template_version_digest_conflict$"):
        repository.publish_template(
            _publication(template=changed_template),
            "platform-admin",
            NOW,
        )


def _install_template_race(
    engine: Engine,
    competitor: CommittedTemplatePublication,
) -> None:
    inserted = False

    @event.listens_for(engine, "before_cursor_execute")
    def insert_competitor(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal inserted
        if inserted or not statement.lstrip().startswith("INSERT INTO adapter_template_revisions"):
            return
        inserted = True
        with engine.begin() as competing_connection:
            competing_connection.execute(
                insert(AdapterTemplateRevisionRecord).values(**_record_values(competitor))
            )


def test_publish_translates_only_expected_same_and_different_digest_races(
    tmp_path: Path,
) -> None:
    for same_digest in (True, False):
        database = tmp_path / f"template-{same_digest}.db"
        engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
        PlatformBase.metadata.create_all(engine)
        incoming = _publication()
        competitor = (
            incoming
            if same_digest
            else _publication(template=_template(resource_profile_id="freqtrade-large"))
        )
        _install_template_race(engine, competitor)
        repository = SqlTemplateRepository(engine, id_factory=SequentialIds())

        if same_digest:
            result = repository.publish_template(incoming, "platform-admin", NOW)
            assert result.payload_digest == incoming.payload_digest
        else:
            with pytest.raises(
                TemplateConflict,
                match=r"^template_version_digest_conflict$",
            ):
                repository.publish_template(incoming, "platform-admin", NOW)
        engine.dispose()


def test_publish_reraises_unrelated_integrity_error_by_identity(
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    forced_error = IntegrityError("forced failure", {}, RuntimeError("unrelated"))

    @event.listens_for(engine, "before_cursor_execute")
    def fail_insert(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("INSERT INTO adapter_template_revisions"):
            raise forced_error

    with pytest.raises(IntegrityError) as exc_info:
        repository.publish_template(_publication(), "platform-admin", NOW)

    assert exc_info.value is forced_error


def test_publish_rolls_back_revision_when_audit_insert_fails(
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    forced_error = IntegrityError("forced audit failure", {}, RuntimeError("unrelated"))

    @event.listens_for(engine, "before_cursor_execute")
    def fail_audit(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("INSERT INTO runtime_audit_events"):
            raise forced_error

    with pytest.raises(IntegrityError) as exc_info:
        repository.publish_template(_publication(), "platform-admin", NOW)

    assert exc_info.value is forced_error
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AdapterTemplateRevisionRecord)) == 0
        assert session.scalar(select(func.count()).select_from(RuntimeAuditEventRecord)) == 0


def test_audit_integrity_error_is_not_reclassified_after_competitor_appears(
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    committed = _publication()
    forced_error = IntegrityError("forced audit failure", {}, RuntimeError("unrelated"))
    competitor_inserted = False

    def fail_audit_insert(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("INSERT INTO runtime_audit_events"):
            raise forced_error

    def insert_competitor_after_rollback(rolled_back_session: Session) -> None:
        nonlocal competitor_inserted
        if competitor_inserted or rolled_back_session.bind is not engine:
            return
        competitor_inserted = True
        with engine.begin() as competing_connection:
            competing_connection.execute(
                insert(AdapterTemplateRevisionRecord).values(
                    **_record_values(committed, published_by="external-competitor")
                )
            )

    event.listen(engine, "before_cursor_execute", fail_audit_insert)
    event.listen(Session, "after_rollback", insert_competitor_after_rollback)
    try:
        with pytest.raises(IntegrityError) as exc_info:
            repository.publish_template(committed, "platform-admin", NOW)
    finally:
        event.remove(Session, "after_rollback", insert_competitor_after_rollback)
        event.remove(engine, "before_cursor_execute", fail_audit_insert)

    assert exc_info.value is forced_error
    assert competitor_inserted
    with Session(engine) as session:
        records = session.scalars(select(AdapterTemplateRevisionRecord)).all()
        assert len(records) == 1
        assert records[0].published_by == "external-competitor"
        assert session.scalar(select(func.count()).select_from(RuntimeAuditEventRecord)) == 0


@pytest.mark.parametrize("corruption", ["digest_mismatch", "non_canonical"])
def test_get_rejects_corrupted_persisted_template_with_stable_error(
    corruption: str,
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    committed = _publication()
    if corruption == "digest_mismatch":
        digest = "f" * 64
        updates = {
            "adapter_template_revision_id": f"template-{digest}",
            "payload_digest": digest,
        }
    else:
        payload = committed.canonical_payload.replace(":", ": ", 1)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        updates = {
            "adapter_template_revision_id": f"template-{digest}",
            "canonical_payload": payload,
            "payload_digest": digest,
        }
    with engine.begin() as connection:
        connection.execute(
            insert(AdapterTemplateRevisionRecord).values(
                **_record_values(committed, **updates)
            )
        )

    with pytest.raises(TemplateDataError, match=r"^invalid_template_revision_data$"):
        repository.get_template_revision(f"template-{digest}")


@pytest.mark.parametrize("transition", ["deprecate", "revoke"])
def test_transition_rejects_corrupted_persisted_template_before_mutation(
    transition: str,
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    committed = _publication()
    revision_id = f"template-{committed.payload_digest}"
    with engine.begin() as connection:
        connection.execute(
            insert(AdapterTemplateRevisionRecord).values(
                **_record_values(
                    committed,
                    status="active",
                    deprecated_at=NOW + timedelta(days=1),
                )
            )
        )
    with Session(engine) as session:
        record = session.get(AdapterTemplateRevisionRecord, revision_id)
        assert record is not None
        before = {
            column.name: getattr(record, column.name)
            for column in AdapterTemplateRevisionRecord.__table__.columns
        }

    with pytest.raises(TemplateDataError, match=r"^invalid_template_revision_data$"):
        if transition == "deprecate":
            repository.deprecate_template(
                revision_id,
                "platform-admin",
                NOW + timedelta(days=2),
            )
        else:
            repository.revoke_template(
                revision_id,
                "platform-admin",
                NOW + timedelta(days=2),
            )

    with Session(engine) as session:
        record = session.get(AdapterTemplateRevisionRecord, revision_id)
        assert record is not None
        after = {
            column.name: getattr(record, column.name)
            for column in AdapterTemplateRevisionRecord.__table__.columns
        }
        assert after == before
        assert session.scalar(select(func.count()).select_from(RuntimeAuditEventRecord)) == 0


def test_status_transitions_are_atomic_immutable_and_idempotent(
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    committed = _publication()
    published = repository.publish_template(committed, "platform-admin", NOW)
    deprecated_at = NOW + timedelta(days=1)
    deprecated = repository.deprecate_template(
        published.revision_id,
        "platform-admin",
        deprecated_at,
    )
    retried = repository.deprecate_template(
        published.revision_id,
        "other-admin",
        deprecated_at + timedelta(days=1),
    )

    assert deprecated.status is TemplateStatus.DEPRECATED
    assert deprecated.deprecated_at == deprecated_at
    assert retried == deprecated

    revoked_at = NOW + timedelta(days=3)
    revoked = repository.revoke_template(
        published.revision_id,
        "platform-admin",
        revoked_at,
    )
    revoke_retry = repository.revoke_template(
        published.revision_id,
        "other-admin",
        revoked_at + timedelta(days=1),
    )

    assert revoked.status is TemplateStatus.REVOKED
    assert revoked.deprecated_at == deprecated_at
    assert revoked.revoked_at == revoked_at
    assert revoke_retry == revoked
    assert revoked.payload_digest == committed.payload_digest
    assert revoked.template == committed.template
    with Session(engine) as session:
        audits = session.scalars(
            select(RuntimeAuditEventRecord).order_by(RuntimeAuditEventRecord.occurred_at)
        ).all()
        assert [audit.action for audit in audits] == [
            "publish_template",
            "deprecate_template",
            "revoke_template",
        ]
        assert audits[1].previous_state == {"status": "active"}
        assert audits[1].next_state == {
            "status": "deprecated",
            "deprecated_at": deprecated_at.isoformat(),
        }
        assert audits[2].next_state == {
            "status": "revoked",
            "deprecated_at": deprecated_at.isoformat(),
            "revoked_at": revoked_at.isoformat(),
        }


def test_revoke_active_is_allowed_but_deprecate_revoked_is_rejected(
    repository: SqlTemplateRepository,
) -> None:
    published = repository.publish_template(_publication(), "platform-admin", NOW)
    revoked = repository.revoke_template(
        published.revision_id,
        "platform-admin",
        NOW + timedelta(days=1),
    )

    assert revoked.status is TemplateStatus.REVOKED
    assert revoked.deprecated_at is None
    with pytest.raises(
        TemplateInvalidTransition,
        match=r"^template_status_transition_invalid$",
    ):
        repository.deprecate_template(
            published.revision_id,
            "platform-admin",
            NOW + timedelta(days=2),
        )


def test_missing_template_and_invalid_inputs_fail_before_session(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SqlTemplateRepository(engine, id_factory=SequentialIds())
    with pytest.raises(TemplateNotFound, match=r"^template_revision_not_found$"):
        repository.get_template_revision("missing-template")

    def fail_if_session_opens(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("session opened before input validation")

    import freqtrade.platform.template_repository as repository_module

    monkeypatch.setattr(repository_module, "Session", fail_if_session_opens)
    with pytest.raises(ValueError, match="template_time_must_be_timezone_aware"):
        repository.publish_template(_publication(), "platform-admin", datetime(2026, 7, 14))
    with pytest.raises(ValidationError):
        repository.publish_template(_publication(), "INVALID ACTOR", NOW)


def test_transition_rolls_back_when_audit_insert_fails(
    engine: Engine,
    repository: SqlTemplateRepository,
) -> None:
    published = repository.publish_template(_publication(), "platform-admin", NOW)
    forced_error = IntegrityError("forced audit failure", {}, RuntimeError("unrelated"))

    @event.listens_for(engine, "before_cursor_execute")
    def fail_audit(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("INSERT INTO runtime_audit_events"):
            raise forced_error

    with pytest.raises(IntegrityError) as exc_info:
        repository.deprecate_template(
            published.revision_id,
            "platform-admin",
            NOW + timedelta(days=1),
        )

    assert exc_info.value is forced_error
    assert repository.get_template_revision(published.revision_id).status is TemplateStatus.ACTIVE


def test_runtime_lifecycle_actions_remain_closed_to_template_audit_actions() -> None:
    assert tuple(RuntimeAction) == ("start", "stop", "retry", "retire")
    assert tuple(RuntimeAuditAction) == (
        "start",
        "stop",
        "retry",
        "retire",
        "publish_template",
        "deprecate_template",
        "revoke_template",
        "register_paper_probe",
    )
    with pytest.raises(ValueError):
        RuntimeAction("publish_template")
    with pytest.raises(ValidationError):
        RuntimeLifecycleCommand(
            instance_id="instance-1",
            action="publish_template",
            idempotency_key="request-1",
            expected_instance_version=0,
        )
