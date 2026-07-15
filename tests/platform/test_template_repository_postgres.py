from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from freqtrade.platform.runtime_models import RuntimeAuditEventRecord
from freqtrade.platform.template_models import AdapterTemplateRevisionRecord
from freqtrade.platform.template_repository import SqlTemplateRepository
from tests.platform.test_template_repository import SequentialIds, _publication


BACKEND_ROOT = Path(__file__).parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic-platform.ini"
NOW = datetime(2026, 7, 14, 9, 30, tzinfo=UTC)


def _config(postgres_url: str) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    return config


def _audit_action_check(postgres_url: str) -> str:
    engine = create_engine(postgres_url)
    try:
        checks = inspect(engine).get_check_constraints("runtime_audit_events")
        return next(
            check["sqltext"]
            for check in checks
            if check["name"] == "ck_runtime_audit_events_action"
        )
    finally:
        engine.dispose()


def test_postgres_migration_upgrades_and_downgrades_template_audit_actions(
    postgres_url: str,
) -> None:
    config = _config(postgres_url)
    command.upgrade(config, "20260712_0002")
    assert "publish_template" not in _audit_action_check(postgres_url)

    command.upgrade(config, "head")
    upgraded_check = _audit_action_check(postgres_url)
    assert all(
        action in upgraded_check
        for action in ("publish_template", "deprecate_template", "revoke_template")
    )

    command.downgrade(config, "20260712_0002")
    downgraded_check = _audit_action_check(postgres_url)
    assert "publish_template" not in downgraded_check
    assert all(action in downgraded_check for action in ("start", "stop", "retry", "retire"))


def test_postgres_repository_enforces_audit_action_and_template_foreign_key(
    postgres_url: str,
) -> None:
    command.upgrade(_config(postgres_url), "head")
    engine = create_engine(postgres_url)
    repository = SqlTemplateRepository(engine, id_factory=SequentialIds())
    committed = _publication()

    view = repository.publish_template(committed, "platform-admin", NOW)

    with Session(engine) as session:
        stored = session.get(AdapterTemplateRevisionRecord, view.revision_id)
        audit = session.scalar(select(RuntimeAuditEventRecord))
        assert stored is not None
        assert audit is not None
        assert audit.adapter_template_revision_id == view.revision_id
        assert audit.action == "publish_template"

    invalid_values = {
        "audit_event_id": "invalid-audit",
        "actor_type": "platform-admin",
        "request_id": "request-invalid",
        "idempotency_key": None,
        "owner_kind": None,
        "owner_id": None,
        "owner_revision": None,
        "instance_id": None,
        "runtime_spec_revision_id": None,
        "adapter_template_revision_id": view.revision_id,
        "action": "invalid_action",
        "previous_state": None,
        "next_state": {"status": "active"},
        "result_code": "invalid",
        "occurred_at": NOW,
        "provenance": {"source": "test"},
    }
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(insert(RuntimeAuditEventRecord).values(**invalid_values))

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                insert(RuntimeAuditEventRecord).values(
                    **{
                        **invalid_values,
                        "audit_event_id": "missing-template-audit",
                        "adapter_template_revision_id": "template-missing",
                        "action": "revoke_template",
                    }
                )
            )
    engine.dispose()
