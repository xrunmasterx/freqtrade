import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from freqtrade.markets.default_catalog import default_catalog_snapshot
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.database import PlatformBase
from freqtrade.platform.runtime_models import (
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
)
from freqtrade.platform.runtime_registration import PAPER_PROBE_INSTANCE_ID
from freqtrade.platform.runtime_registration_repository import (
    PaperProbeRegistrationConflict,
    SqlPaperProbeRegistrationRepository,
)
from freqtrade.platform.template_domain import TemplateStatus
from freqtrade.platform.template_models import (
    RuntimeSpecRevisionRecord,
    SecretReferenceRecord,
    StateAllocationRecord,
)
from freqtrade.platform.template_repository import (
    PostgresTemplateTransactionLock,
    SqlTemplateRepository,
)
from tests.platform.test_runtime_registration_repository import _publication, _request


BACKEND_ROOT = Path(__file__).parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic-platform.ini"
NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)


@pytest.fixture
def postgres_engine(postgres_url: str) -> Engine:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _published_request(engine: Engine):
    revision = SqlTemplateRepository(engine).publish_template(
        _publication(),
        "platform-admin",
        NOW,
    )
    return _request(revision.revision_id)


def _published_requests_for_distinct_revisions(engine: Engine):
    repository = SqlTemplateRepository(engine)
    first_publication = _publication()
    first_revision = repository.publish_template(
        first_publication,
        "platform-admin",
        NOW,
    )
    second_template = first_publication.template.model_copy(
        update={"semantic_version": "1.0.1"}
    )
    second_payload = json.dumps(
        {"schema_version": 1, **second_template.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"
    second_publication = first_publication.model_copy(
        update={
            "template": second_template,
            "canonical_payload": second_payload,
            "payload_digest": hashlib.sha256(second_payload.encode()).hexdigest(),
        }
    )
    second_revision = repository.publish_template(
        second_publication,
        "platform-admin",
        NOW,
    )
    return _request(first_revision.revision_id), _request(second_revision.revision_id)


def _advisory_lock_key(identity: str) -> int:
    digest = hashlib.sha256(f"adapter-template:{identity}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class _SqlMarkerLock:
    def acquire(self, session: Session, revision_id: str) -> None:
        session.execute(
            text(f"SELECT 1 /* PG_ADVISORY_XACT_LOCK:{revision_id} */")
        )


class _OrderRecorderLock:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    def acquire(self, session: Session, revision_id: str) -> None:
        del session, revision_id
        self._statements.append("SELECT PG_ADVISORY_XACT_LOCK")


def test_registration_locks_template_then_fixed_identity_before_fixed_reads() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PlatformBase.metadata.create_all(engine)
    try:
        request = _published_request(engine)
        repository = SqlPaperProbeRegistrationRepository(engine)
        repository._transaction_lock = _SqlMarkerLock()
        statements: list[str] = []

        def capture_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement.upper())

        event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            repository.ensure_paper_probe_registration(request, "operator_cli", NOW)
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)

        assert statements[0].endswith(
            f"PG_ADVISORY_XACT_LOCK:{request.adapter_template_revision_id.upper()} */"
        )
        assert "FROM ADAPTER_TEMPLATE_REVISIONS" in statements[1]
        assert statements[2].endswith(
            f"PG_ADVISORY_XACT_LOCK:{PAPER_PROBE_INSTANCE_ID.upper()} */"
        )
        fixed_work_index = next(
            index
            for index, statement in enumerate(statements)
            if "CATALOG_REVISIONS" in statement
        )
        assert fixed_work_index > 2
    finally:
        engine.dispose()


def test_template_transition_acquires_lock_before_for_update_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PlatformBase.metadata.create_all(engine)
    try:
        request = _published_request(engine)
        statements: list[str] = []
        repository = SqlTemplateRepository(engine)
        repository._transaction_lock = _OrderRecorderLock(statements)
        original_scalar = Session.scalar

        def record_scalar(
            session: Session,
            statement: object,
            *args: object,
            **kwargs: object,
        ):
            statements.append(
                str(statement.compile(dialect=postgresql.dialect())).upper()
            )
            return original_scalar(session, statement, *args, **kwargs)

        monkeypatch.setattr(Session, "scalar", record_scalar)

        repository.deprecate_template(
            request.adapter_template_revision_id,
            "platform-admin",
            NOW,
        )

        assert "PG_ADVISORY_XACT_LOCK" in statements[0]
        row_lock_index = next(
            index for index, statement in enumerate(statements) if "FOR UPDATE" in statement
        )
        assert row_lock_index > 0
    finally:
        engine.dispose()


def test_postgres_registration_waits_for_exact_template_transaction_lock(
    postgres_engine: Engine,
) -> None:
    request = _published_request(postgres_engine)
    repository = SqlPaperProbeRegistrationRepository(postgres_engine)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with Session(postgres_engine) as blocker, blocker.begin():
            PostgresTemplateTransactionLock().acquire(
                blocker,
                request.adapter_template_revision_id,
            )
            future = executor.submit(
                repository.ensure_paper_probe_registration,
                request,
                "operator_cli",
                NOW,
            )
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
        result = future.result(timeout=5)
    finally:
        executor.shutdown(wait=True)

    assert result.adapter_template_revision_id == request.adapter_template_revision_id


def test_postgres_template_transition_uses_same_transaction_lock(
    postgres_engine: Engine,
) -> None:
    request = _published_request(postgres_engine)
    repository = SqlTemplateRepository(postgres_engine)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with Session(postgres_engine) as blocker, blocker.begin():
            PostgresTemplateTransactionLock().acquire(
                blocker,
                request.adapter_template_revision_id,
            )
            future = executor.submit(
                repository.deprecate_template,
                request.adapter_template_revision_id,
                "platform-admin",
                NOW,
            )
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
        result = future.result(timeout=5)
    finally:
        executor.shutdown(wait=True)

    assert result.status is TemplateStatus.DEPRECATED


def test_postgres_template_transition_locks_before_for_update(
    postgres_engine: Engine,
) -> None:
    request = _published_request(postgres_engine)
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.upper())

    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        SqlTemplateRepository(postgres_engine).deprecate_template(
            request.adapter_template_revision_id,
            "platform-admin",
            NOW,
        )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)

    advisory_lock_index = next(
        index
        for index, statement in enumerate(statements)
        if "PG_ADVISORY_XACT_LOCK" in statement
    )
    row_lock_index = next(
        index for index, statement in enumerate(statements) if "FOR UPDATE" in statement
    )
    assert advisory_lock_index == 0
    assert row_lock_index > advisory_lock_index


def test_postgres_concurrent_registration_is_idempotent(
    postgres_engine: Engine,
) -> None:
    request = _published_request(postgres_engine)
    repository = SqlPaperProbeRegistrationRepository(postgres_engine)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                repository.ensure_paper_probe_registration,
                request,
                f"operator_{index}",
                NOW,
            )
            for index in range(2)
        ]
        results = [future.result(timeout=5) for future in futures]

    assert results[0] == results[1]
    with Session(postgres_engine) as session:
        assert session.scalar(
            select(func.count()).where(
                RuntimeAuditEventRecord.action == "register_paper_probe"
            )
        ) == 1


def test_postgres_distinct_template_revisions_serialize_fixed_registration(
    postgres_engine: Engine,
) -> None:
    requests = _published_requests_for_distinct_revisions(postgres_engine)
    snapshot = default_catalog_snapshot()
    with Session(postgres_engine) as session, session.begin():
        session.add(
            CatalogRevisionRecord(
                revision_id=snapshot.revision_id,
                payload=snapshot.model_dump(mode="json"),
                created_at=NOW,
            )
        )

    repository = SqlPaperProbeRegistrationRepository(postgres_engine)
    executor = ThreadPoolExecutor(max_workers=2)
    identity_lock_attempts = 0
    identity_lock_attempts_guard = Lock()
    both_identity_locks_attempted = Event()

    def capture_identity_lock_attempt(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal identity_lock_attempts
        if (
            "PG_ADVISORY_XACT_LOCK" not in statement.upper()
            or not isinstance(parameters, dict)
            or parameters["lock_key"] != _advisory_lock_key(PAPER_PROBE_INSTANCE_ID)
        ):
            return
        with identity_lock_attempts_guard:
            identity_lock_attempts += 1
            if identity_lock_attempts == 2:
                both_identity_locks_attempted.set()

    listener_installed = False
    try:
        with Session(postgres_engine) as blocker, blocker.begin():
            PostgresTemplateTransactionLock().acquire(
                blocker,
                PAPER_PROBE_INSTANCE_ID,
            )
            event.listen(
                postgres_engine,
                "before_cursor_execute",
                capture_identity_lock_attempt,
            )
            listener_installed = True
            futures = [
                executor.submit(
                    repository.ensure_paper_probe_registration,
                    request,
                    f"operator_{index}",
                    NOW,
                )
                for index, request in enumerate(requests)
            ]
            assert both_identity_locks_attempted.wait(timeout=5)
            for future in futures:
                with pytest.raises(FutureTimeoutError):
                    future.result(timeout=0.2)

        results = []
        conflicts = []
        for future in futures:
            try:
                results.append(future.result(timeout=5))
            except PaperProbeRegistrationConflict as error:
                conflicts.append(error)
    finally:
        if listener_installed:
            event.remove(
                postgres_engine,
                "before_cursor_execute",
                capture_identity_lock_attempt,
            )
        executor.shutdown(wait=True)

    assert len(results) == 1
    assert [str(error) for error in conflicts] == [
        "paper_probe_registration_conflict"
    ]
    assert results[0].adapter_template_revision_id in {
        request.adapter_template_revision_id for request in requests
    }
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(CatalogRevisionRecord)) == 1
        assert session.scalar(select(func.count()).select_from(StateAllocationRecord)) == 1
        assert session.scalar(select(func.count()).select_from(SecretReferenceRecord)) == 3
        assert session.scalar(select(func.count()).select_from(RuntimeSpecRevisionRecord)) == 1
        assert session.scalar(select(func.count()).select_from(RuntimeInstanceRecord)) == 1
        assert session.scalar(
            select(func.count()).where(
                RuntimeAuditEventRecord.action == "register_paper_probe"
            )
        ) == 1


def test_postgres_catalog_publish_race_uses_insert_if_absent_without_update(
    postgres_engine: Engine,
) -> None:
    request = _published_request(postgres_engine)
    repository = SqlPaperProbeRegistrationRepository(postgres_engine)
    snapshot = default_catalog_snapshot()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with Session(postgres_engine) as publisher, publisher.begin():
            publisher.add(
                CatalogRevisionRecord(
                    revision_id=snapshot.revision_id,
                    payload=snapshot.model_dump(mode="json"),
                    created_at=NOW,
                )
            )
            publisher.flush()
            future = executor.submit(
                repository.ensure_paper_probe_registration,
                request,
                "operator_cli",
                NOW,
            )
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
        result = future.result(timeout=5)
    finally:
        executor.shutdown(wait=True)

    assert result.catalog_revision_id == snapshot.revision_id


def test_postgres_registration_uses_no_row_lock_or_update_permission(
    postgres_engine: Engine,
) -> None:
    request = _published_request(postgres_engine)
    statements: list[tuple[str, object]] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append((statement.upper(), parameters))

    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        SqlPaperProbeRegistrationRepository(
            postgres_engine
        ).ensure_paper_probe_registration(request, "operator_cli", NOW)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)

    first_statement, first_parameters = statements[0]
    assert "PG_ADVISORY_XACT_LOCK" in first_statement
    assert isinstance(first_parameters, dict)
    assert first_parameters["lock_key"] == _advisory_lock_key(
        request.adapter_template_revision_id
    )
    template_read_index = next(
        index
        for index, (statement, _) in enumerate(statements)
        if "FROM ADAPTER_TEMPLATE_REVISIONS" in statement
    )
    identity_lock_index = next(
        index
        for index, (statement, parameters) in enumerate(statements)
        if "PG_ADVISORY_XACT_LOCK" in statement
        and isinstance(parameters, dict)
        and parameters["lock_key"] == _advisory_lock_key(PAPER_PROBE_INSTANCE_ID)
    )
    fixed_work_index = next(
        index
        for index, (statement, _) in enumerate(statements)
        if "CATALOG_REVISIONS" in statement
    )
    assert template_read_index == 1
    assert identity_lock_index == 2
    assert fixed_work_index > identity_lock_index
    assert all("FOR UPDATE" not in statement for statement, _ in statements)
    assert all(
        not statement.lstrip().startswith("UPDATE ") for statement, _ in statements
    )
