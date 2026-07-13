from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from freqtrade.markets import CatalogSnapshot
from freqtrade.platform.catalog_repository import SqlCatalogRepository
from freqtrade.platform.runtime_domain import (
    Identifier,
    RuntimeAttemptView,
    RuntimeInstanceView,
    RuntimeJobView,
)
from freqtrade.platform.runtime_repository import SqlRuntimeRepository


@runtime_checkable
class PlatformControlQueryRepository(Protocol):
    def ready(self) -> bool: ...

    def current_catalog(self) -> CatalogSnapshot: ...

    def get_instance(self, instance_id: Identifier) -> RuntimeInstanceView: ...

    def list_instances(self) -> tuple[RuntimeInstanceView, ...]: ...

    def list_attempts(self, instance_id: Identifier) -> tuple[RuntimeAttemptView, ...]: ...

    def list_jobs(self, instance_id: Identifier) -> tuple[RuntimeJobView, ...]: ...


class SqlPlatformControlQueryRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._catalog = SqlCatalogRepository(engine)
        self._runtime = SqlRuntimeRepository(engine)

    def ready(self) -> bool:
        try:
            with self._engine.connect() as connection:
                return connection.scalar(text("SELECT 1")) == 1
        except SQLAlchemyError:
            return False

    def current_catalog(self) -> CatalogSnapshot:
        return self._catalog.current()

    def get_instance(self, instance_id: Identifier) -> RuntimeInstanceView:
        return self._runtime.get_instance(instance_id)

    def list_instances(self) -> tuple[RuntimeInstanceView, ...]:
        return self._runtime.list_instances()

    def list_attempts(self, instance_id: Identifier) -> tuple[RuntimeAttemptView, ...]:
        return self._runtime.list_attempts(instance_id)

    def list_jobs(self, instance_id: Identifier) -> tuple[RuntimeJobView, ...]:
        return self._runtime.list_jobs(instance_id)


class _ResponseModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RuntimeInstancesResponse(_ResponseModel):
    instances: tuple[RuntimeInstanceView, ...]


class RuntimeAttemptsResponse(_ResponseModel):
    instance_id: Identifier
    attempts: tuple[RuntimeAttemptView, ...]


class RuntimeJobsResponse(_ResponseModel):
    instance_id: Identifier
    jobs: tuple[RuntimeJobView, ...]
