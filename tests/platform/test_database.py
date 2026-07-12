from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from freqtrade.platform import database as database_module
from freqtrade.platform.database import (
    PlatformDatabaseSettings,
    create_platform_engine,
    platform_session,
)


def test_database_settings_build_url_from_exact_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "password"
    secret.write_text("correct-horse-battery-staple\n", encoding="utf-8")

    settings = PlatformDatabaseSettings(
        host="platform-postgres",
        port=5432,
        database="platform",
        username="platform_control",
        password_file=secret,
    )

    assert settings.sqlalchemy_url().render_as_string(hide_password=True) == (
        "postgresql+psycopg://platform_control:***@platform-postgres:5432/platform"
    )


def test_database_settings_reject_non_postgres_production_settings(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="PostgreSQL is required"):
        PlatformDatabaseSettings(
            host="sqlite",
            port=1,
            database="test",
            username="test",
            password_file=tmp_path / "password",
        )


def test_create_platform_engine_enables_connection_health_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = tmp_path / "password"
    secret.write_text("secret", encoding="utf-8")
    settings = PlatformDatabaseSettings(
        host="platform-postgres",
        port=5432,
        database="platform",
        username="platform_control",
        password_file=secret,
    )
    expected_engine = object()
    calls = []

    def record_create_engine(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return expected_engine

    monkeypatch.setattr(database_module, "create_engine", record_create_engine)

    engine = create_platform_engine(settings)

    assert engine is expected_engine
    assert calls == [
        (
            (settings.sqlalchemy_url(),),
            {"pool_pre_ping": True, "future": True},
        )
    ]


def test_platform_session_disables_expiration_after_commit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with platform_session(engine) as session:
        assert session.bind is engine
        assert session.expire_on_commit is False
