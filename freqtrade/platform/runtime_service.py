from pydantic import TypeAdapter

from freqtrade.platform.runtime_domain import Identifier, RuntimeJobView, RuntimeLifecycleCommand
from freqtrade.platform.runtime_repository import RuntimeRepository


_ACTOR_ADAPTER = TypeAdapter(Identifier)


class RuntimeApplicationService:
    def __init__(self, repository: RuntimeRepository) -> None:
        self._repository = repository

    def request(
        self,
        command: RuntimeLifecycleCommand,
        actor: Identifier,
    ) -> RuntimeJobView:
        validated_actor = _ACTOR_ADAPTER.validate_python(actor)
        return self._repository.create_job(command, validated_actor)
