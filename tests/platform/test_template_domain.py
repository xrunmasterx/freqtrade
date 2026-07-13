import hashlib
import importlib
import json

import pytest
from pydantic import ValidationError

from freqtrade.platform.runtime_domain import RuntimeOwnerKind, RuntimeOwnerRef


def _domain_modules():
    return (
        importlib.import_module("freqtrade.platform.template_domain"),
        importlib.import_module("freqtrade.platform.runtime_spec"),
    )


def _template_values() -> dict[str, object]:
    return {
        "template_id": "freqtrade-bot",
        "semantic_version": "1.2.3",
        "allowed_instance_kinds": ("execution-worker",),
        "allowed_owner_kinds": (RuntimeOwnerKind.PAPER_PROBE,),
        "allowed_environments": ("paper", "live"),
        "image_policy_id": "signed-freqtrade-image",
        "command_policy_id": "freqtrade-supervisor-command",
        "mount_policy_ids": ("runtime-userdata",),
        "network_policy_id": "internal-runtime-network",
        "health_profile_id": "freqtrade-http-health",
        "resource_profile_id": "paper-probe-small",
        "secret_classes": ("exchange-api",),
        "state_layout_id": "freqtrade-userdata-v1",
    }


def _owner_scope() -> RuntimeOwnerRef:
    return RuntimeOwnerRef(
        owner_kind=RuntimeOwnerKind.PAPER_PROBE,
        owner_id="paper-probe-1",
        owner_revision="paper-probe-v1",
    )


def test_public_contracts_are_exported_with_closed_enum_values() -> None:
    domain, runtime_spec = _domain_modules()
    platform = importlib.import_module("freqtrade.platform")
    expected_exports = {
        "AdapterTemplate",
        "FrozenPlatformModel",
        "RuntimeSpecRevision",
        "SecretReference",
        "SecretReferenceStatus",
        "StateAllocation",
        "StateAllocationKind",
        "StateAllocationStatus",
        "TemplateStatus",
    }

    assert expected_exports <= set(platform.__all__)
    assert all(getattr(platform, name) is not None for name in expected_exports)
    assert {status.value for status in domain.TemplateStatus} == {
        "active",
        "deprecated",
        "revoked",
    }
    assert {status.value for status in domain.StateAllocationStatus} == {
        "reserved",
        "provisioning",
        "ready",
        "quarantined",
        "retired",
    }
    assert {kind.value for kind in domain.StateAllocationKind} == {"fresh", "restored"}
    assert {status.value for status in domain.SecretReferenceStatus} == {
        "active",
        "disabled",
        "retired",
    }
    assert runtime_spec.RuntimeSpecRevision is platform.RuntimeSpecRevision


def test_adapter_template_is_exact_frozen_and_rejects_container_power_fields() -> None:
    domain, _runtime_spec = _domain_modules()
    template = domain.AdapterTemplate(**_template_values())

    assert set(type(template).model_fields) == set(_template_values())
    assert template.model_config["frozen"] is True
    assert template.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        template.template_id = "changed"

    forbidden_fields = (
        "image",
        "command",
        "host_path",
        "mount_source",
        "port",
        "network_name",
        "device",
        "privileged",
        "capabilities",
        "compose_fragment",
        "project_name",
        "environment",
    )
    for field_name in forbidden_fields:
        with pytest.raises(ValidationError):
            domain.AdapterTemplate(**_template_values(), **{field_name: "forbidden"})


@pytest.mark.parametrize(
    "semantic_version",
    ["1.2", "v1.2.3", "1.2.3-beta", "1.2.3+build", "01.2.3"],
)
def test_adapter_template_accepts_only_plain_semantic_versions(semantic_version: str) -> None:
    domain, _runtime_spec = _domain_modules()

    with pytest.raises(ValidationError):
        domain.AdapterTemplate(**{**_template_values(), "semantic_version": semantic_version})


@pytest.mark.parametrize(
    ("field_name", "duplicate_value"),
    [
        ("allowed_instance_kinds", "execution-worker"),
        ("allowed_owner_kinds", RuntimeOwnerKind.PAPER_PROBE),
        ("allowed_environments", "paper"),
        ("mount_policy_ids", "runtime-userdata"),
        ("secret_classes", "exchange-api"),
    ],
)
def test_adapter_template_requires_nonempty_unique_tuples(
    field_name: str,
    duplicate_value: object,
) -> None:
    domain, _runtime_spec = _domain_modules()

    with pytest.raises(ValidationError):
        domain.AdapterTemplate(**{**_template_values(), field_name: ()})
    with pytest.raises(ValidationError):
        domain.AdapterTemplate(
            **{**_template_values(), field_name: (duplicate_value, duplicate_value)}
        )


def test_runtime_spec_revision_canonicalizes_hashes_and_freezes_payload() -> None:
    _domain, runtime_spec = _domain_modules()
    first_payload = {"z": 1, "a": {"y": [2, 1], "x": "汉字"}}
    second_payload = {"a": {"x": "汉字", "y": [2, 1]}, "z": 1}
    expected_json = json.dumps(
        first_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected_digest = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()

    first = runtime_spec.RuntimeSpecRevision.from_payload(first_payload)
    second = runtime_spec.RuntimeSpecRevision.from_payload(second_payload)
    first_payload["new"] = "later-mutation"

    assert first == second
    assert first.canonical_payload == expected_json
    assert isinstance(first.canonical_payload, str)
    assert first.payload_digest == expected_digest
    assert first.runtime_spec_revision_id == f"runtime-spec-{expected_digest}"
    assert set(type(first).model_fields) == {
        "runtime_spec_revision_id",
        "canonical_payload",
        "payload_digest",
    }
    assert "later-mutation" not in first.canonical_payload
    with pytest.raises(ValidationError):
        first.canonical_payload = "{}"
    with pytest.raises(ValidationError):
        runtime_spec.RuntimeSpecRevision(
            **first.model_dump(),
            compiler_option="forbidden",
        )


def test_state_allocation_derives_only_the_platform_relative_path() -> None:
    domain, _runtime_spec = _domain_modules()
    allocation_values = {
        "state_allocation_id": "state-allocation-1",
        "instance_id": "instance-1",
        "layout_id": "freqtrade-userdata-v1",
        "provider_id": "managed-local-v1",
        "kind": domain.StateAllocationKind.FRESH,
        "status": domain.StateAllocationStatus.RESERVED,
        "generation": 1,
    }
    allocation = domain.StateAllocation(
        **allocation_values,
        restore_source_bundle_id=None,
    )
    allocation_without_restore = domain.StateAllocation(
        **allocation_values,
    )

    assert allocation_without_restore.restore_source_bundle_id is None

    assert set(type(allocation).model_fields) == {
        "state_allocation_id",
        "instance_id",
        "layout_id",
        "provider_id",
        "kind",
        "status",
        "generation",
        "restore_source_bundle_id",
    }
    assert allocation.relative_path == "ft_userdata/runtime/instances/instance-1"
    assert allocation.model_dump()["relative_path"] == allocation.relative_path
    for field_name in ("path", "relative_path", "host_path"):
        with pytest.raises(ValidationError):
            domain.StateAllocation(
                **allocation.model_dump(exclude={"relative_path"}),
                **{field_name: "caller-path"},
            )
    with pytest.raises(ValidationError):
        domain.StateAllocation(
            **{**allocation.model_dump(exclude={"relative_path"}), "generation": 0}
        )
    with pytest.raises(ValidationError):
        domain.StateAllocation(
            **{
                **allocation.model_dump(exclude={"relative_path"}),
                "provider_id": "caller-provider",
            }
        )


def test_secret_reference_contains_no_secret_material_or_location() -> None:
    domain, _runtime_spec = _domain_modules()
    reference = domain.SecretReference(
        secret_reference_id="secret-reference-1",
        provider_id="local-file-v1",
        secret_class="exchange-api",
        logical_name="primary-exchange",
        owner_scope=_owner_scope(),
        status=domain.SecretReferenceStatus.ACTIVE,
    )

    assert set(type(reference).model_fields) == {
        "secret_reference_id",
        "provider_id",
        "secret_class",
        "logical_name",
        "owner_scope",
        "status",
    }
    assert reference.model_config["frozen"] is True
    assert reference.model_config["extra"] == "forbid"
    for field_name in (
        "secret_value",
        "credential",
        "content_hash",
        "secret_path",
        "host_path",
    ):
        with pytest.raises(ValidationError):
            domain.SecretReference(**reference.model_dump(), **{field_name: "forbidden"})
    with pytest.raises(ValidationError):
        domain.SecretReference(**{**reference.model_dump(), "provider_id": "unknown-provider"})
