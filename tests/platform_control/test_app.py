import importlib
import inspect
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from freqtrade.markets import default_catalog_snapshot
from freqtrade.platform.catalog_repository import SqlCatalogRepository
from freqtrade.platform.database import PlatformBase, PlatformDatabaseSettings
from freqtrade.platform.runtime_domain import (
    RuntimeAttemptView,
    RuntimeInstanceView,
    RuntimeJobView,
    RuntimeOwnerRef,
)
from freqtrade.platform.runtime_repository import RuntimeDataError, RuntimeNotFound
from freqtrade.platform_control.api_runtime import (
    PlatformControlQueryRepository,
    SqlPlatformControlQueryRepository,
)
from freqtrade.platform_control.app import create_platform_app
from freqtrade.platform_control.settings import PlatformControlSettings


USERNAME = "platform_operator"
API_PASSWORD = "platform-api-password"
JWT_SECRET = "platform-jwt-secret-that-is-at-least-32-characters"
NOW = datetime(2026, 7, 12, 8, tzinfo=UTC)


def _write(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def _settings(tmp_path: Path) -> PlatformControlSettings:
    return PlatformControlSettings(
        username=USERNAME,
        api_password_file=_write(tmp_path / "api-password", API_PASSWORD),
        jwt_secret_file=_write(tmp_path / "jwt-secret", JWT_SECRET),
        database=PlatformDatabaseSettings(
            host="database.internal",
            port=5432,
            database="platform_control",
            username="platform_control",
            password_file=_write(tmp_path / "database-password", "database-password"),
        ),
    )


def _instance() -> RuntimeInstanceView:
    return RuntimeInstanceView(
        instance_id="instance-1",
        instance_kind="execution_worker",
        owner_ref=RuntimeOwnerRef(
            owner_kind="paper_probe",
            owner_id="owner-1",
            owner_revision="owner-revision-1",
        ),
        management_mode="supervisor",
        runtime_spec_revision_id="runtime-spec-1",
        environment="paper",
        state_allocation_id="state-allocation-1",
        desired_state="stopped",
        lifecycle_status="registered",
        failure_latched=False,
        optimistic_version=0,
        created_at=NOW,
        retired_at=None,
    )


def _attempt() -> RuntimeAttemptView:
    return RuntimeAttemptView(
        attempt_id="attempt-1",
        instance_id="instance-1",
        attempt_number=1,
        runtime_spec_revision_id="runtime-spec-1",
        adapter_template_revision_id="adapter-template-1",
        status="stopped",
        health_result="healthy",
        started_at=NOW,
        stopped_at=NOW,
        exit_code=0,
        failure_code=None,
    )


def _job() -> RuntimeJobView:
    return RuntimeJobView(
        job_id="job-1",
        instance_id="instance-1",
        requested_action="start",
        idempotency_key="key-1",
        expected_instance_version=0,
        status="succeeded",
        lease_owner=None,
        lease_generation=1,
        lease_expires_at=None,
        requested_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        failure_code=None,
    )


class FakeQueryRepository:
    def __init__(self) -> None:
        self.is_ready = True
        self.catalog = default_catalog_snapshot().model_copy(
            update={"revision_id": "sql-catalog-1"}
        )
        self.instance = _instance()
        self.calls: list[str] = []

    def ready(self) -> bool:
        self.calls.append("ready")
        return self.is_ready

    def current_catalog(self):
        self.calls.append("current_catalog")
        return self.catalog

    def get_instance(self, instance_id: str) -> RuntimeInstanceView:
        self.calls.append(f"get_instance:{instance_id}")
        if instance_id != self.instance.instance_id:
            raise RuntimeNotFound("runtime_instance_not_found")
        return self.instance

    def list_instances(self) -> tuple[RuntimeInstanceView, ...]:
        self.calls.append("list_instances")
        return (self.instance,)

    def list_attempts(self, instance_id: str) -> tuple[RuntimeAttemptView, ...]:
        self.calls.append(f"list_attempts:{instance_id}")
        return (_attempt(),)

    def list_jobs(self, instance_id: str) -> tuple[RuntimeJobView, ...]:
        self.calls.append(f"list_jobs:{instance_id}")
        return (_job(),)


@pytest.fixture
def repository() -> FakeQueryRepository:
    return FakeQueryRepository()


@pytest.fixture
def client(tmp_path: Path, repository: FakeQueryRepository) -> TestClient:
    return TestClient(create_platform_app(_settings(tmp_path), repository))


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v2/token/login", auth=(USERNAME, API_PASSWORD))
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_exact_routes_openapi_methods_and_no_lifecycle_or_access_surface(
    client: TestClient,
) -> None:
    openapi = client.app.openapi()
    paths = openapi["paths"]
    assert set(paths) == {
        "/api/v2/ping",
        "/api/v2/token/login",
        "/api/v2/token/refresh",
        "/api/v2/catalog",
        "/api/v2/runtime-instances",
        "/api/v2/runtime-instances/{instance_id}",
        "/api/v2/runtime-instances/{instance_id}/attempts",
        "/api/v2/runtime-instances/{instance_id}/jobs",
    }
    for path, operations in paths.items():
        expected = {"post"} if "/token/" in path else {"get"}
        assert set(operations) == expected

    job_schema = openapi["components"]["schemas"]["RuntimeJobView"]
    assert "lease_generation" in job_schema["required"]
    assert job_schema["properties"]["lease_generation"] == {
        "minimum": 0.0,
        "title": "Lease Generation",
        "type": "integer",
    }

    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/v2/runtime-instances").status_code == 405
    for forbidden in (
        "/api/v2/runtime-access/instance-1",
        "/api/v2/proxy",
        "/api/v2/runtime-instances/instance-1/start",
    ):
        assert client.get(forbidden).status_code == 404


def test_schema_http_routes_are_disabled_and_external_routes_match_allowlist(
    client: TestClient,
) -> None:
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404

    def collect_routes(routes, prefix: str = "") -> dict[str, set[str]]:
        observed: dict[str, set[str]] = {}
        for route in routes:
            methods = getattr(route, "methods", None)
            if methods:
                observed.setdefault(f"{prefix}{route.path}", set()).update(methods)
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                nested_prefix = f"{prefix}{route.include_context.prefix}"
                for path, nested_methods in collect_routes(
                    original_router.routes,
                    nested_prefix,
                ).items():
                    observed.setdefault(path, set()).update(nested_methods)
        return observed

    observed = collect_routes(client.app.routes)
    assert observed == {
        "/api/v2/ping": {"GET", "HEAD"},
        "/api/v2/token/login": {"POST"},
        "/api/v2/token/refresh": {"POST"},
        "/api/v2/catalog": {"GET", "HEAD"},
        "/api/v2/runtime-instances": {"GET", "HEAD"},
        "/api/v2/runtime-instances/{instance_id}": {"GET", "HEAD"},
        "/api/v2/runtime-instances/{instance_id}/attempts": {"GET", "HEAD"},
        "/api/v2/runtime-instances/{instance_id}/jobs": {"GET", "HEAD"},
    }


def test_ping_is_public_and_all_other_reads_share_auth(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    assert client.get("/api/v2/ping").json() == {"status": "pong"}
    protected = (
        "/api/v2/catalog",
        "/api/v2/runtime-instances",
        "/api/v2/runtime-instances/instance-1",
        "/api/v2/runtime-instances/instance-1/attempts",
        "/api/v2/runtime-instances/instance-1/jobs",
    )
    for path in protected:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth_headers).status_code == 200


@pytest.mark.parametrize("method", ["get", "head"])
@pytest.mark.parametrize(
    "path,authenticated",
    [
        ("/api/v2/ping", False),
        ("/api/v2/catalog", False),
        ("/api/v2/catalog", True),
        ("/api/v2/runtime-instances", False),
        ("/api/v2/runtime-instances", True),
        ("/api/v2/runtime-instances/instance-1", False),
        ("/api/v2/runtime-instances/instance-1", True),
        ("/api/v2/runtime-instances/instance-1/attempts", False),
        ("/api/v2/runtime-instances/instance-1/attempts", True),
        ("/api/v2/runtime-instances/instance-1/jobs", False),
        ("/api/v2/runtime-instances/instance-1/jobs", True),
    ],
)
def test_nonempty_query_is_rejected_before_auth_and_repository_without_logging_values(
    client: TestClient,
    repository: FakeQueryRepository,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    authenticated: bool,
) -> None:
    sentinel = "phase2a-sensitive-query-sentinel"
    before = list(repository.calls)
    caplog.set_level(logging.INFO, logger="freqtrade.platform_control")

    response = getattr(client, method)(
        f"{path}?private_key={sentinel}",
        headers=auth_headers if authenticated else None,
    )

    assert response.status_code == 400
    if method == "head":
        assert response.content == b""
        assert response.headers["content-type"] == "application/json"
    else:
        assert response.json() == {"detail": "unexpected_query_parameters"}
    assert repository.calls == before
    application_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(("freqtrade.platform_control", "uvicorn"))
    )
    assert sentinel not in application_logs
    assert "private_key" not in application_logs


def test_token_routes_reject_nonempty_query_before_auth_processing(
    client: TestClient,
) -> None:
    sentinel = "phase2a-sensitive-token-query"
    login = client.post(
        f"/api/v2/token/login?private_key={sentinel}",
        auth=(USERNAME, API_PASSWORD),
    )
    refresh = client.post(f"/api/v2/token/refresh?private_key={sentinel}")

    for response in (login, refresh):
        assert response.status_code == 400
        assert response.json() == {"detail": "unexpected_query_parameters"}
        assert sentinel not in response.text


def test_read_shapes_use_sql_catalog_and_exact_runtime_wrappers(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    assert client.get("/api/v2/catalog", headers=auth_headers).json()["revision_id"] == (
        "sql-catalog-1"
    )
    instances = client.get("/api/v2/runtime-instances", headers=auth_headers).json()
    assert set(instances) == {"instances"}
    assert instances["instances"][0]["instance_id"] == "instance-1"
    detail = client.get("/api/v2/runtime-instances/instance-1", headers=auth_headers).json()
    assert detail["instance_id"] == "instance-1"
    attempts = client.get(
        "/api/v2/runtime-instances/instance-1/attempts", headers=auth_headers
    ).json()
    assert set(attempts) == {"instance_id", "attempts"}
    assert attempts["attempts"][0]["attempt_id"] == "attempt-1"
    jobs = client.get("/api/v2/runtime-instances/instance-1/jobs", headers=auth_headers).json()
    assert set(jobs) == {"instance_id", "jobs"}
    assert jobs["jobs"][0]["job_id"] == "job-1"
    assert jobs["jobs"][0]["lease_generation"] == 1


def test_head_executes_same_auth_and_query_boundary_but_is_schema_hidden(
    client: TestClient,
    repository: FakeQueryRepository,
    auth_headers: dict[str, str],
) -> None:
    public = client.head("/api/v2/ping")
    assert public.status_code == 200 and public.content == b""
    protected = {
        "/api/v2/catalog": "current_catalog",
        "/api/v2/runtime-instances": "list_instances",
        "/api/v2/runtime-instances/instance-1": "get_instance:instance-1",
        "/api/v2/runtime-instances/instance-1/attempts": "list_attempts:instance-1",
        "/api/v2/runtime-instances/instance-1/jobs": "list_jobs:instance-1",
    }
    for path, expected_call in protected.items():
        assert client.head(path).status_code == 401
        before = len(repository.calls)
        response = client.head(path, headers=auth_headers)
        assert response.status_code == 200 and response.content == b""
        assert expected_call in repository.calls[before:]


def test_child_routes_resolve_instance_before_querying_children(
    client: TestClient,
    repository: FakeQueryRepository,
    auth_headers: dict[str, str],
) -> None:
    response = client.get("/api/v2/runtime-instances/unknown/attempts", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "runtime_instance_not_found"}
    assert repository.calls[-1] == "get_instance:unknown"
    assert "list_attempts:unknown" not in repository.calls


def test_stable_registry_catalog_and_control_plane_errors_hide_exception_text(
    client: TestClient,
    repository: FakeQueryRepository,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_registry():
        raise RuntimeDataError("private evidence should never escape")

    monkeypatch.setattr(repository, "list_instances", invalid_registry)
    invalid = client.get("/api/v2/runtime-instances", headers=auth_headers)
    assert invalid.status_code == 500
    assert invalid.json() == {"detail": "invalid_registry_data"}
    assert "private evidence" not in invalid.text

    def unavailable_catalog():
        raise LookupError("private catalog state")

    monkeypatch.setattr(repository, "current_catalog", unavailable_catalog)
    catalog = client.get("/api/v2/catalog", headers=auth_headers)
    assert catalog.status_code == 503
    assert catalog.json() == {"detail": "catalog_unavailable"}
    assert "private catalog state" not in catalog.text

    def invalid_catalog_validation():
        return type(repository.catalog).model_validate({"revision_id": "private invalid catalog"})

    monkeypatch.setattr(repository, "current_catalog", invalid_catalog_validation)
    invalid_catalog = client.get("/api/v2/catalog", headers=auth_headers)
    assert invalid_catalog.status_code == 503
    assert invalid_catalog.json() == {"detail": "catalog_unavailable"}
    assert "private invalid catalog" not in invalid_catalog.text

    def database_error(_instance_id: str):
        raise OperationalError("SELECT private", {"password": "private"}, Exception("private"))

    monkeypatch.setattr(repository, "list_jobs", database_error)
    control = client.get("/api/v2/runtime-instances/instance-1/jobs", headers=auth_headers)
    assert control.status_code == 503
    assert control.json() == {"detail": "control_plane_unavailable"}
    assert "private" not in control.text


@pytest.mark.parametrize(
    "operation,path",
    [
        ("list_instances", "/api/v2/runtime-instances"),
        ("list_attempts", "/api/v2/runtime-instances/instance-1/attempts"),
        ("list_jobs", "/api/v2/runtime-instances/instance-1/jobs"),
    ],
    ids=["management-mode", "attempt-status", "job-status"],
)
def test_persisted_enum_corruption_has_stable_api_error_without_evidence(
    client: TestClient,
    repository: FakeQueryRepository,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    path: str,
) -> None:
    private_value = "private-corrupt-enum-secret SELECT traceback"

    def invalid_registry(*_args):
        raise RuntimeDataError(private_value) from None

    monkeypatch.setattr(repository, operation, invalid_registry)

    response = client.get(path, headers=auth_headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "invalid_registry_data"}
    assert private_value not in response.text
    assert "SELECT" not in response.text
    assert "traceback" not in response.text


def test_ping_fails_readiness_closed_without_exception_details(
    client: TestClient,
    repository: FakeQueryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository.is_ready = False
    unavailable = client.get("/api/v2/ping")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "control_plane_unavailable"}

    def database_error():
        raise OperationalError("SELECT private", {}, Exception("private database"))

    monkeypatch.setattr(repository, "ready", database_error)
    failed = client.get("/api/v2/ping")
    assert failed.status_code == 503
    assert failed.json() == {"detail": "control_plane_unavailable"}
    assert "private" not in failed.text


def test_sql_query_composition_uses_database_catalog_and_has_read_only_public_surface() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PlatformBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_runtime_attempt_active")
        connection.exec_driver_sql("DROP INDEX uq_runtime_job_active")
    snapshot = default_catalog_snapshot().model_copy(update={"revision_id": "sql-catalog-1"})
    SqlCatalogRepository(engine).publish(snapshot, created_at=NOW)
    repository = SqlPlatformControlQueryRepository(engine)
    try:
        assert isinstance(repository, PlatformControlQueryRepository)
        assert repository.ready() is True
        assert repository.current_catalog() == snapshot
        public = {name for name in dir(repository) if not name.startswith("_")}
        assert public == {
            "current_catalog",
            "get_instance",
            "list_attempts",
            "list_instances",
            "list_jobs",
            "ready",
        }
        for forbidden in (
            "create_job",
            "claim_next_job",
            "complete_job",
            "append_audit",
            "engine",
            "session",
            "runtime_repository",
            "lifecycle_repository",
        ):
            assert not hasattr(repository, forbidden)
    finally:
        engine.dispose()


def test_response_models_are_frozen_and_extra_forbid() -> None:
    from pydantic import ValidationError

    from freqtrade.platform_control.api_runtime import RuntimeInstancesResponse

    response = RuntimeInstancesResponse(instances=(_instance(),))
    with pytest.raises(ValidationError, match="Instance is frozen"):
        response.instances = ()
    with pytest.raises(ValidationError):
        RuntimeInstancesResponse.model_validate({"instances": [], "private": "forbidden"})


def test_imports_have_no_startup_side_effect_and_sources_have_no_forbidden_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: started.append((args, kwargs)))
    sys.modules.pop("freqtrade.platform_control.__main__", None)

    module = importlib.import_module("freqtrade.platform_control.__main__")

    assert started == []
    assert callable(module.main)
    package_root = Path(inspect.getfile(module)).parent
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_root.glob("*.py"))
    for forbidden in (
        "get_api_config",
        "create_all(",
        "alembic",
        "docker",
        "Bot",
        "user_data",
        "runtime_access",
    ):
        assert forbidden not in source


def test_main_disables_uvicorn_access_log(monkeypatch: pytest.MonkeyPatch) -> None:
    from freqtrade.platform_control import __main__ as main_module

    settings = SimpleNamespace(
        database=object(),
        listen_host="127.0.0.1",
        listen_port=8090,
    )
    app = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(main_module.PlatformControlSettings, "from_env", lambda: settings)
    monkeypatch.setattr(main_module, "create_platform_engine", lambda _settings: object())
    monkeypatch.setattr(main_module, "SqlPlatformControlQueryRepository", lambda _engine: object())
    monkeypatch.setattr(main_module, "create_platform_app", lambda *_args: app)
    monkeypatch.setattr(
        main_module.uvicorn,
        "run",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs),
    )

    main_module.main()

    assert captured == {
        "args": (app,),
        "kwargs": {"host": "127.0.0.1", "port": 8090, "access_log": False},
    }


def test_app_has_no_global_pydantic_validation_error_handler(client: TestClient) -> None:
    from pydantic import ValidationError

    assert ValidationError not in client.app.exception_handlers
