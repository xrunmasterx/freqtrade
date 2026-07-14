from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from freqtrade.platform.runtime_domain import RuntimeLifecycleCommand
from freqtrade.platform.runtime_registration import EnsurePaperProbeRegistrationRequest
from freqtrade.platform.runtime_service import (
    RuntimeApplicationService,
    RuntimeServiceConfigurationError,
)


def _command() -> RuntimeLifecycleCommand:
    return RuntimeLifecycleCommand(
        instance_id="instance-1",
        action="start",
        idempotency_key="key-1",
        expected_instance_version=0,
    )


def _registration_request() -> EnsurePaperProbeRegistrationRequest:
    return EnsurePaperProbeRegistrationRequest(
        adapter_template_revision_id="template-" + "a" * 64,
        component_commits={
            "root_commit": "1" * 40,
            "backend_commit": "2" * 40,
            "frontend_commit": "3" * 40,
            "strategies_commit": "4" * 40,
        },
        config_blob_digest="b" * 64,
        strategy_digest="c" * 64,
        safety_policy_digest="d" * 64,
        strategy_class_name="SampleStrategy",
        closed_policy_snapshot={
            "image_policy_ids": ["freqtrade-reviewed-image-v1"],
            "command_policy_ids": ["freqtrade-spot-paper-v1"],
            "mount_policy_ids": ["managed-state-rw-v1"],
            "network_policy_ids": ["isolated-public-market-data-v1"],
            "health_profile_ids": ["freqtrade-ping-v1"],
            "resource_profile_ids": ["freqtrade-small-v1"],
            "state_layout_ids": ["freqtrade-state-v1"],
            "source_commit": "1" * 40,
        },
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


def test_application_service_delegates_template_publication_through_keyword_dependency() -> None:
    publication = object()
    occurred_at = datetime(2026, 7, 14, 10, tzinfo=UTC)
    template_repository = Mock()
    expected = object()
    template_repository.publish_template.return_value = expected
    service = RuntimeApplicationService(template_repository=template_repository)

    result = service.publish_template(publication, "operator_cli", occurred_at)

    assert result is expected
    template_repository.publish_template.assert_called_once_with(
        publication,
        "operator_cli",
        occurred_at,
    )


def test_application_service_delegates_registration_and_status_through_keyword_dependency() -> None:
    occurred_at = datetime(2026, 7, 14, 10, tzinfo=UTC)
    registration_repository = Mock()
    expected_registration = object()
    expected_status = object()
    registration_repository.ensure_paper_probe_registration.return_value = expected_registration
    registration_repository.registration_status.return_value = expected_status
    service = RuntimeApplicationService(registration_repository=registration_repository)
    request = _registration_request()

    result = service.ensure_paper_probe_registration(request, "operator_cli", occurred_at)
    status = service.registration_status("phase2-spot-paper-probe")

    assert result is expected_registration
    assert status is expected_status
    registration_repository.ensure_paper_probe_registration.assert_called_once_with(
        request,
        "operator_cli",
        occurred_at,
    )
    registration_repository.registration_status.assert_called_once_with(
        "phase2-spot-paper-probe"
    )


@pytest.mark.parametrize(
    "operation",
    ["request", "publish_template", "ensure_paper_probe_registration", "registration_status"],
)
def test_application_service_reports_one_stable_missing_dependency_error(operation: str) -> None:
    service = RuntimeApplicationService()
    occurred_at = datetime(2026, 7, 14, 10, tzinfo=UTC)

    with pytest.raises(
        RuntimeServiceConfigurationError,
        match=r"^runtime_service_not_configured$",
    ):
        if operation == "request":
            service.request(_command(), "operator_cli")
        elif operation == "publish_template":
            service.publish_template(object(), "operator_cli", occurred_at)
        elif operation == "ensure_paper_probe_registration":
            service.ensure_paper_probe_registration(
                _registration_request(),
                "operator_cli",
                occurred_at,
            )
        else:
            service.registration_status("phase2-spot-paper-probe")
