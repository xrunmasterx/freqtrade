from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from freqtrade.markets import default_catalog_snapshot
from freqtrade.platform.database import PlatformDatabaseSettings
from freqtrade.platform.runtime_repository import RuntimeNotFound
from freqtrade.platform_control.app import create_platform_app
from freqtrade.platform_control.settings import (
    PlatformControlSecretError,
    PlatformControlSettings,
    PlatformControlSettingsError,
    load_platform_secrets,
)


USERNAME = "platform_operator"
API_PASSWORD = "platform-api-password"
JWT_SECRET = "platform-jwt-secret-that-is-at-least-32-characters"


class EmptyRepository:
    def ready(self) -> bool:
        return True

    def current_catalog(self):
        return default_catalog_snapshot()

    def get_instance(self, _instance_id: str):
        raise RuntimeNotFound("runtime_instance_not_found")

    def list_instances(self) -> tuple:
        return ()

    def list_attempts(self, _instance_id: str) -> tuple:
        return ()

    def list_jobs(self, _instance_id: str) -> tuple:
        return ()


def _write(path: Path, value: str | bytes) -> Path:
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _settings(
    tmp_path: Path,
    *,
    api_value: str = API_PASSWORD,
    jwt_value: str = JWT_SECRET,
) -> PlatformControlSettings:
    api_path = _write(tmp_path / "api-password", api_value)
    jwt_path = _write(tmp_path / "jwt-secret", jwt_value)
    database_path = _write(tmp_path / "database-password", "database-password")
    return PlatformControlSettings(
        username=USERNAME,
        api_password_file=api_path,
        jwt_secret_file=jwt_path,
        database=PlatformDatabaseSettings(
            host="database.internal",
            port=5432,
            database="platform_control",
            username="platform_control",
            password_file=database_path,
        ),
    )


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_platform_app(_settings(tmp_path), EmptyRepository()))


def test_settings_from_env_uses_only_fixed_names_and_redacts_secret_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    api_path = _write(tmp_path / "api-password", API_PASSWORD)
    jwt_path = _write(tmp_path / "jwt-secret", JWT_SECRET)
    database_path = _write(tmp_path / "database-password", "database-password")
    values = {
        "PLATFORM_CONTROL_USERNAME": USERNAME,
        "PLATFORM_CONTROL_API_PASSWORD_FILE": str(api_path),
        "PLATFORM_CONTROL_JWT_SECRET_FILE": str(jwt_path),
        "PLATFORM_DATABASE_HOST": "database.internal",
        "PLATFORM_DATABASE_PORT": "5432",
        "PLATFORM_DATABASE_NAME": "platform_control",
        "PLATFORM_DATABASE_USERNAME": "platform_control",
        "PLATFORM_DATABASE_PASSWORD_FILE": str(database_path),
        "PLATFORM_CONTROL_UNSUPPORTED_SECRET": "must-be-ignored",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = PlatformControlSettings.from_env()

    assert settings.listen_host == "127.0.0.1"
    assert settings.listen_port == 8090
    assert settings.username == USERNAME
    assert settings.database.host == "database.internal"
    rendered = f"{settings!r} {settings.model_dump()}"
    for hidden in (str(api_path), str(jwt_path), str(database_path), API_PASSWORD, JWT_SECRET):
        assert hidden not in rendered


@pytest.mark.parametrize("listen_host", ["localhost", "0.0.0.0", "::", "192.168.1.5"])
def test_settings_reject_non_literal_loopback_without_echo(
    tmp_path: Path,
    listen_host: str,
) -> None:
    valid = _settings(tmp_path)
    values = {
        "listen_host": listen_host,
        "listen_port": valid.listen_port,
        "username": valid.username,
        "api_password_file": valid.api_password_file,
        "jwt_secret_file": valid.jwt_secret_file,
        "database": valid.database,
    }

    with pytest.raises(PlatformControlSettingsError) as exc_info:
        PlatformControlSettings.from_values(values)

    assert str(exc_info.value) == "invalid_platform_control_settings"
    assert listen_host not in str(exc_info.value)


@pytest.mark.parametrize("listen_port", [0, 65536, True])
def test_settings_reject_invalid_ports_with_stable_error(
    tmp_path: Path,
    listen_port: object,
) -> None:
    valid = _settings(tmp_path)
    values = {
        "listen_host": valid.listen_host,
        "listen_port": listen_port,
        "username": valid.username,
        "api_password_file": valid.api_password_file,
        "jwt_secret_file": valid.jwt_secret_file,
        "database": valid.database,
    }

    with pytest.raises(PlatformControlSettingsError, match=r"^invalid_platform_control_settings$"):
        PlatformControlSettings.from_values(values)


def test_settings_reject_missing_env_and_path_aliases_without_disclosure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (
        "PLATFORM_CONTROL_USERNAME",
        "PLATFORM_CONTROL_API_PASSWORD_FILE",
        "PLATFORM_CONTROL_JWT_SECRET_FILE",
        "PLATFORM_DATABASE_HOST",
        "PLATFORM_DATABASE_PORT",
        "PLATFORM_DATABASE_NAME",
        "PLATFORM_DATABASE_USERNAME",
        "PLATFORM_DATABASE_PASSWORD_FILE",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(PlatformControlSettingsError, match=r"^invalid_platform_control_settings$"):
        PlatformControlSettings.from_env()

    shared = _write(tmp_path / "shared-secret", JWT_SECRET)
    database_path = _write(tmp_path / "database-password", "database-password")
    database = PlatformDatabaseSettings(
        host="database.internal",
        port=5432,
        database="platform_control",
        username="platform_control",
        password_file=database_path,
    )
    for api_path, jwt_path, selected_database in (
        (shared, shared, database),
        (
            database_path,
            _write(tmp_path / "separate-jwt", JWT_SECRET),
            database,
        ),
    ):
        values = {
            "username": USERNAME,
            "api_password_file": api_path,
            "jwt_secret_file": jwt_path,
            "database": selected_database,
        }
        with pytest.raises(PlatformControlSettingsError) as exc_info:
            PlatformControlSettings.from_values(values)
        assert str(exc_info.value) == "invalid_platform_control_settings"
        assert str(api_path) not in str(exc_info.value)


@pytest.mark.parametrize(
    "content",
    ["", "line-one\nline-two", "line-one\rline-two", "nul\x00value", b"\xff\xfe"],
    ids=["empty", "embedded-lf", "embedded-cr", "nul", "decode"],
)
def test_secret_reader_rejects_invalid_content_without_path_or_value(
    tmp_path: Path,
    content: str | bytes,
) -> None:
    settings = _settings(tmp_path)
    _write(settings.api_password_file, content)

    with pytest.raises(PlatformControlSecretError) as exc_info:
        load_platform_secrets(settings)

    assert str(exc_info.value) == "invalid_platform_control_secret"
    assert str(settings.api_password_file) not in str(exc_info.value)
    assert "line-one" not in str(exc_info.value)


def test_secret_reader_trims_only_trailing_newlines_and_rejects_equal_values(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, api_value=f"{API_PASSWORD}\r\n", jwt_value=f"{JWT_SECRET}\n")

    secrets = load_platform_secrets(settings)

    rendered = repr(secrets)
    assert API_PASSWORD not in rendered
    assert JWT_SECRET not in rendered
    assert str(settings.api_password_file) not in rendered

    for equal_value in (JWT_SECRET, "密" * 32):
        equal_settings = _settings(tmp_path, api_value=equal_value, jwt_value=equal_value)
        with pytest.raises(PlatformControlSecretError, match=r"^invalid_platform_control_secret$"):
            load_platform_secrets(equal_settings)


def test_secret_reader_requires_long_jwt_secret(tmp_path: Path) -> None:
    settings = _settings(tmp_path, jwt_value="short-jwt-secret")

    with pytest.raises(PlatformControlSecretError, match=r"^invalid_platform_control_secret$"):
        load_platform_secrets(settings)


def test_login_access_and_refresh_preserve_exact_hs256_payload_contract(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/api/v2/token/login", auth=(USERNAME, API_PASSWORD))

    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "refresh_token"}
    access_payload = jwt.decode(response.json()["access_token"], JWT_SECRET, algorithms=["HS256"])
    refresh_payload = jwt.decode(response.json()["refresh_token"], JWT_SECRET, algorithms=["HS256"])
    assert access_payload["identity"] == {"u": USERNAME}
    assert refresh_payload["identity"] == {"u": USERNAME}
    assert access_payload["type"] == "access"
    assert refresh_payload["type"] == "refresh"
    assert access_payload["exp"] - access_payload["iat"] == 15 * 60
    assert refresh_payload["exp"] - refresh_payload["iat"] == 30 * 24 * 60 * 60

    access = response.json()["access_token"]
    refresh = response.json()["refresh_token"]
    assert (
        client.get(
            "/api/v2/runtime-instances", headers={"Authorization": f"Bearer {access}"}
        ).status_code
        == 200
    )
    assert client.get("/api/v2/runtime-instances", auth=(USERNAME, API_PASSWORD)).status_code == 200
    refreshed = client.post("/api/v2/token/refresh", headers={"Authorization": f"Bearer {refresh}"})
    assert refreshed.status_code == 200
    assert set(refreshed.json()) == {"access_token"}

    assert (
        client.post(
            "/api/v2/token/refresh", headers={"Authorization": f"Bearer {access}"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v2/runtime-instances", headers={"Authorization": f"Bearer {refresh}"}
        ).status_code
        == 401
    )


def test_auth_rejects_wrong_identity_algorithm_expiry_signature_basic_and_query(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    now = datetime.now(UTC)
    payload = {
        "identity": {"u": "different_user"},
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "type": "access",
    }
    tokens = (
        jwt.encode(payload, JWT_SECRET, algorithm="HS256"),
        jwt.encode(
            {**payload, "identity": ["private malformed identity"]},
            JWT_SECRET,
            algorithm="HS256",
        ),
        jwt.encode(
            {**payload, "identity": {"u": 12345}},
            JWT_SECRET,
            algorithm="HS256",
        ),
        jwt.encode({**payload, "identity": {"u": USERNAME}}, JWT_SECRET, algorithm="HS384"),
        jwt.encode(
            {**payload, "identity": {"u": USERNAME}, "exp": now - timedelta(seconds=1)},
            JWT_SECRET,
            algorithm="HS256",
        ),
        jwt.encode(
            {**payload, "identity": {"u": USERNAME}},
            "wrong-signing-secret-that-is-at-least-32-characters",
            algorithm="HS256",
        ),
    )
    for token in tokens:
        response = client.get(
            "/api/v2/runtime-instances", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "unauthorized"}
        assert token not in response.text

    assert client.post("/api/v2/token/login", auth=(USERNAME, "wrong-password")).status_code == 401
    query = client.get(
        "/api/v2/runtime-instances",
        params={"username": USERNAME, "password": API_PASSWORD, "token": tokens[0]},
    )
    assert query.status_code == 401
    assert API_PASSWORD not in query.text


def test_basic_auth_compares_both_username_and_password(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from freqtrade.platform_control import auth as auth_module

    client = _client(tmp_path)
    compared: list[tuple[object, object]] = []
    original = auth_module.secrets.compare_digest

    def record_compare(left: object, right: object) -> bool:
        compared.append((left, right))
        return original(left, right)

    monkeypatch.setattr(auth_module.secrets, "compare_digest", record_compare)
    response = client.post("/api/v2/token/login", auth=("wrong_user", "wrong_password"))

    assert response.status_code == 401
    assert len(compared) == 2


def test_secrets_are_absent_from_settings_responses_and_openapi(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_platform_app(settings, EmptyRepository()))
    response = client.post("/api/v2/token/login", auth=(USERNAME, API_PASSWORD))
    rendered = " ".join(
        (
            repr(settings),
            str(settings.model_dump()),
            str(client.app.openapi()),
            response.text,
        )
    )

    for hidden in (
        API_PASSWORD,
        JWT_SECRET,
        str(settings.api_password_file),
        str(settings.jwt_secret_file),
        str(settings.database.password_file),
    ):
        assert hidden not in rendered
