from datetime import datetime
from typing import Protocol

from pydantic import TypeAdapter

from freqtrade.platform.runtime_domain import Identifier, RuntimeJobView, RuntimeLifecycleCommand
from freqtrade.platform.runtime_registration import (
    EnsurePaperProbeRegistrationRequest,
    PaperProbeRegistrationRepository,
    PaperProbeRegistrationResult,
    PaperProbeRegistrationStatus,
)
from freqtrade.platform.runtime_repository import (
    ResolvedRuntimeMaterial as ResolvedRuntimeMaterial,
)
from freqtrade.platform.runtime_repository import RuntimeRepository
from freqtrade.platform.template_repository import (
    AdapterTemplateRevisionView,
    CommittedTemplatePublication,
)


_ACTOR_ADAPTER = TypeAdapter(Identifier)
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


class RuntimeServiceConfigurationError(RuntimeError):
    pass


class TemplatePublicationRepository(Protocol):
    def publish_template(
        self,
        committed_template: CommittedTemplatePublication,
        actor: Identifier,
        published_at: datetime,
    ) -> AdapterTemplateRevisionView: ...


class RuntimeApplicationService:
    def __init__(
        self,
        repository: RuntimeRepository | None = None,
        *,
        template_repository: TemplatePublicationRepository | None = None,
        registration_repository: PaperProbeRegistrationRepository | None = None,
    ) -> None:
        self._repository = repository
        self._template_repository = template_repository
        self._registration_repository = registration_repository

    def request(
        self,
        command: RuntimeLifecycleCommand,
        actor: Identifier,
    ) -> RuntimeJobView:
        if self._repository is None:
            raise RuntimeServiceConfigurationError("runtime_service_not_configured")
        validated_actor = _ACTOR_ADAPTER.validate_python(actor)
        return self._repository.create_job(command, validated_actor)

    def publish_template(
        self,
        publication: CommittedTemplatePublication,
        actor: Identifier,
        occurred_at: datetime,
    ) -> AdapterTemplateRevisionView:
        if self._template_repository is None:
            raise RuntimeServiceConfigurationError("runtime_service_not_configured")
        validated_actor = _ACTOR_ADAPTER.validate_python(actor)
        return self._template_repository.publish_template(
            publication,
            validated_actor,
            occurred_at,
        )

    def ensure_paper_probe_registration(
        self,
        request: EnsurePaperProbeRegistrationRequest,
        actor: Identifier,
        occurred_at: datetime,
    ) -> PaperProbeRegistrationResult:
        if self._registration_repository is None:
            raise RuntimeServiceConfigurationError("runtime_service_not_configured")
        validated_actor = _ACTOR_ADAPTER.validate_python(actor)
        return self._registration_repository.ensure_paper_probe_registration(
            request,
            validated_actor,
            occurred_at,
        )

    def registration_status(self, instance_id: Identifier) -> PaperProbeRegistrationStatus:
        if self._registration_repository is None:
            raise RuntimeServiceConfigurationError("runtime_service_not_configured")
        validated_instance_id = _IDENTIFIER_ADAPTER.validate_python(instance_id)
        return self._registration_repository.registration_status(validated_instance_id)
