import importlib

import pytest
from pydantic import ValidationError


def test_registration_module_exposes_closed_paper_probe_contract() -> None:
    registration = importlib.import_module("freqtrade.platform.runtime_registration")

    request = registration.EnsurePaperProbeRegistrationRequest(
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

    assert set(type(request).model_fields) == {
        "adapter_template_revision_id",
        "component_commits",
        "config_blob_digest",
        "strategy_digest",
        "safety_policy_digest",
        "strategy_class_name",
        "closed_policy_snapshot",
    }
    assert request.model_config["frozen"] is True
    assert request.model_config["extra"] == "forbid"
    assert registration.PAPER_PROBE_INSTANCE_ID == "phase2-spot-paper-probe"
    assert registration.PAPER_PROBE_OWNER_REVISION == "phase2-spot-paper-probe-v1"
    assert registration.PAPER_PROBE_STATE_ALLOCATION_ID == "state-phase2-spot-paper-probe-v1"
    assert registration.PAPER_PROBE_AUDIT_EVENT_ID == "audit-register-phase2-spot-paper-probe"
    assert registration.PAPER_PROBE_REQUEST_ID == "request-register-phase2-spot-paper-probe"
    assert registration.PAPER_PROBE_SECRET_REFERENCE_IDS == (
        "secret-phase2-spot-paper-probe-api-password-v1",
        "secret-phase2-spot-paper-probe-jwt-secret-v1",
        "secret-phase2-spot-paper-probe-ws-token-v1",
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("strategy_class_name", "OtherStrategy"),
        ("config_blob_digest", "A" * 64),
        ("instance_id", "caller-owned-instance"),
        ("environment", "live"),
    ],
)
def test_registration_request_rejects_non_contract_input(field_name: str, value: str) -> None:
    registration = importlib.import_module("freqtrade.platform.runtime_registration")
    values = {
        "adapter_template_revision_id": "template-" + "a" * 64,
        "component_commits": {
            "root_commit": "1" * 40,
            "backend_commit": "2" * 40,
            "frontend_commit": "3" * 40,
            "strategies_commit": "4" * 40,
        },
        "config_blob_digest": "b" * 64,
        "strategy_digest": "c" * 64,
        "safety_policy_digest": "d" * 64,
        "strategy_class_name": "SampleStrategy",
        "closed_policy_snapshot": {
            "image_policy_ids": ["freqtrade-reviewed-image-v1"],
            "command_policy_ids": ["freqtrade-spot-paper-v1"],
            "mount_policy_ids": ["managed-state-rw-v1"],
            "network_policy_ids": ["isolated-public-market-data-v1"],
            "health_profile_ids": ["freqtrade-ping-v1"],
            "resource_profile_ids": ["freqtrade-small-v1"],
            "state_layout_ids": ["freqtrade-state-v1"],
            "source_commit": "1" * 40,
        },
        field_name: value,
    }

    with pytest.raises(ValidationError):
        registration.EnsurePaperProbeRegistrationRequest.model_validate(values)


def test_registration_result_and_status_expose_only_stable_identity() -> None:
    registration = importlib.import_module("freqtrade.platform.runtime_registration")

    assert registration.PaperProbeRegistrationResult is registration.PaperProbeRegistrationStatus
    assert set(registration.PaperProbeRegistrationStatus.model_fields) == {
        "instance_id",
        "runtime_spec_revision_id",
        "adapter_template_revision_id",
        "catalog_revision_id",
        "state_allocation_id",
        "secret_reference_ids",
        "desired_state",
        "lifecycle_status",
    }


def test_registration_contract_is_exported_from_platform_package() -> None:
    import freqtrade.platform as platform

    assert platform.EnsurePaperProbeRegistrationRequest is not None
    assert platform.PaperProbeRegistrationResult is platform.PaperProbeRegistrationStatus
    assert platform.PaperProbeRegistrationConflict is not None
    assert platform.PaperProbeRegistrationNotFound is not None
    assert platform.SqlPaperProbeRegistrationRepository is not None
