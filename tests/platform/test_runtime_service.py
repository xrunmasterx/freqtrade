from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from freqtrade.platform.runtime_domain import RuntimeLifecycleCommand
from freqtrade.platform.runtime_service import RuntimeApplicationService


def _command() -> RuntimeLifecycleCommand:
    return RuntimeLifecycleCommand(
        instance_id="instance-1",
        action="start",
        idempotency_key="key-1",
        expected_instance_version=0,
    )


def test_application_service_delegates_validated_command_and_actor() -> None:
    repository = Mock()
    expected = object()
    repository.create_job.return_value = expected
    service = RuntimeApplicationService(repository)
    command = _command()

    result = service.request(command, "operator_cli")

    assert result is expected
    repository.create_job.assert_called_once_with(command, "operator_cli")


@pytest.mark.parametrize(
    "actor",
    ["", "Operator CLI", "operator/cli", "operator_cli\ncredential"],
)
def test_application_service_rejects_invalid_actor_before_repository_access(actor: str) -> None:
    repository = Mock()
    service = RuntimeApplicationService(repository)

    with pytest.raises(ValidationError):
        service.request(_command(), actor)

    repository.create_job.assert_not_called()
