from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from freqtrade.platform.runtime_domain import Identifier, RuntimeInstanceView
from freqtrade.platform.runtime_repository import RuntimeDataError, RuntimeNotFound
from freqtrade.platform_control.api_runtime import (
    PlatformControlQueryRepository,
    RuntimeAttemptsResponse,
    RuntimeInstancesResponse,
    RuntimeJobsResponse,
)
from freqtrade.platform_control.auth import create_auth_router, create_platform_user_dependency
from freqtrade.platform_control.settings import PlatformControlSettings, load_platform_secrets
from freqtrade.rpc.api_server.api_schemas import CatalogResponse, Ping


_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


def _identifier(value: str) -> str:
    try:
        return _IDENTIFIER_ADAPTER.validate_python(value)
    except ValidationError:
        raise RuntimeNotFound("runtime_instance_not_found") from None


def _service_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def create_platform_app(  # noqa: C901
    settings: PlatformControlSettings,
    repository: PlatformControlQueryRepository,
) -> FastAPI:
    platform_secrets = load_platform_secrets(settings)
    require_platform_user = create_platform_user_dependency(settings, platform_secrets)
    app = FastAPI(
        title="Platform Control",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )

    @app.exception_handler(RuntimeNotFound)
    async def runtime_not_found(_request, _error: RuntimeNotFound):
        return _json_error(status.HTTP_404_NOT_FOUND, "runtime_instance_not_found")

    @app.exception_handler(RuntimeDataError)
    async def invalid_registry_data(_request, _error: RuntimeDataError):
        return _json_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "invalid_registry_data")

    @app.exception_handler(ValidationError)
    async def invalid_registry_validation(_request, _error: ValidationError):
        return _json_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "invalid_registry_data")

    @app.exception_handler(SQLAlchemyError)
    async def control_plane_unavailable(_request, _error: SQLAlchemyError):
        return _json_error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_plane_unavailable")

    app.include_router(create_auth_router(settings, platform_secrets), prefix="/api/v2")
    router = APIRouter(dependencies=[Depends(require_platform_user)])

    def ping_query() -> Ping:
        if not repository.ready():
            raise _service_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "control_plane_unavailable",
            )
        return Ping(status="pong")

    @app.get("/api/v2/ping", response_model=Ping)
    def ping() -> Ping:
        return ping_query()

    @app.head("/api/v2/ping", include_in_schema=False)
    def ping_head() -> Response:
        ping_query()
        return Response()

    def catalog_query() -> CatalogResponse:
        try:
            snapshot = repository.current_catalog()
        except (LookupError, ValidationError):
            raise _service_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "catalog_unavailable",
            ) from None
        return CatalogResponse(**snapshot.model_dump())

    @router.get("/catalog", response_model=CatalogResponse)
    def catalog() -> CatalogResponse:
        return catalog_query()

    @router.head("/catalog", include_in_schema=False)
    def catalog_head() -> Response:
        catalog_query()
        return Response()

    def instances_query() -> RuntimeInstancesResponse:
        return RuntimeInstancesResponse(instances=repository.list_instances())

    @router.get("/runtime-instances", response_model=RuntimeInstancesResponse)
    def runtime_instances() -> RuntimeInstancesResponse:
        return instances_query()

    @router.head("/runtime-instances", include_in_schema=False)
    def runtime_instances_head() -> Response:
        instances_query()
        return Response()

    def instance_query(instance_id: str) -> RuntimeInstanceView:
        return repository.get_instance(_identifier(instance_id))

    @router.get("/runtime-instances/{instance_id}", response_model=RuntimeInstanceView)
    def runtime_instance(instance_id: str) -> RuntimeInstanceView:
        return instance_query(instance_id)

    @router.head("/runtime-instances/{instance_id}", include_in_schema=False)
    def runtime_instance_head(instance_id: str) -> Response:
        instance_query(instance_id)
        return Response()

    def attempts_query(instance_id: str) -> RuntimeAttemptsResponse:
        validated_id = _identifier(instance_id)
        repository.get_instance(validated_id)
        return RuntimeAttemptsResponse(
            instance_id=validated_id,
            attempts=repository.list_attempts(validated_id),
        )

    @router.get(
        "/runtime-instances/{instance_id}/attempts",
        response_model=RuntimeAttemptsResponse,
    )
    def runtime_attempts(instance_id: str) -> RuntimeAttemptsResponse:
        return attempts_query(instance_id)

    @router.head("/runtime-instances/{instance_id}/attempts", include_in_schema=False)
    def runtime_attempts_head(instance_id: str) -> Response:
        attempts_query(instance_id)
        return Response()

    def jobs_query(instance_id: str) -> RuntimeJobsResponse:
        validated_id = _identifier(instance_id)
        repository.get_instance(validated_id)
        return RuntimeJobsResponse(
            instance_id=validated_id,
            jobs=repository.list_jobs(validated_id),
        )

    @router.get(
        "/runtime-instances/{instance_id}/jobs",
        response_model=RuntimeJobsResponse,
    )
    def runtime_jobs(instance_id: str) -> RuntimeJobsResponse:
        return jobs_query(instance_id)

    @router.head("/runtime-instances/{instance_id}/jobs", include_in_schema=False)
    def runtime_jobs_head(instance_id: str) -> Response:
        jobs_query(instance_id)
        return Response()

    app.include_router(router, prefix="/api/v2")
    return app


def _json_error(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})
