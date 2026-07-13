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


def _runtime_spec_envelope(canonical_payload: str) -> dict[str, str]:
    payload_digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return {
        "runtime_spec_revision_id": f"runtime-spec-{payload_digest}",
        "canonical_payload": canonical_payload,
        "payload_digest": payload_digest,
    }


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
    assert template.model_config["hide_input_in_errors"] is True
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


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "secret_value",
        "secret_values",
        "secret_path",
        "secret_paths",
        "secret_content",
        "secret_content_hash",
        "secret_hash",
        "credential",
        "credentials",
        "password",
        "passwords",
        "token",
        "tokens",
        "api_key",
        "api_secret",
        "private_key",
        "authorization",
        "cookie",
        "dsn",
        "host_path",
        "host_paths",
    ],
)
def test_runtime_spec_rejects_top_level_sensitive_keys_without_echoing_values(
    forbidden_key: str,
) -> None:
    _domain, runtime_spec = _domain_modules()
    secret_marker = "fictional-runtime-secret-value"

    with pytest.raises(ValueError) as exc_info:
        runtime_spec.RuntimeSpecRevision.from_payload({forbidden_key: secret_marker})

    assert str(exc_info.value) == "runtime_spec_sensitive_key_forbidden"
    assert secret_marker not in str(exc_info.value)
    assert secret_marker not in repr(exc_info.value)


def test_runtime_spec_recurses_lists_and_allows_secret_reference_identity() -> None:
    _domain, runtime_spec = _domain_modules()
    secret_marker = "fictional-nested-runtime-secret"

    with pytest.raises(ValueError) as exc_info:
        runtime_spec.RuntimeSpecRevision.from_payload(
            {"outer": [{"nested": {"api_secret": secret_marker}}]}
        )

    assert str(exc_info.value) == "runtime_spec_sensitive_key_forbidden"
    assert secret_marker not in repr(exc_info.value)

    revision = runtime_spec.RuntimeSpecRevision.from_payload(
        {"secret_reference_ids": ["secret-reference-1"]}
    )
    assert "secret-reference-1" in revision.canonical_payload


def test_runtime_spec_direct_construction_cannot_bypass_sensitive_key_boundary() -> None:
    _domain, runtime_spec = _domain_modules()
    secret_marker = "fictional-direct-runtime-secret"
    canonical_payload = json.dumps(
        {"nested": {"password": secret_marker}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    with pytest.raises(ValidationError) as exc_info:
        runtime_spec.RuntimeSpecRevision(**_runtime_spec_envelope(canonical_payload))

    assert "runtime_spec_sensitive_key_forbidden" in str(exc_info.value)
    assert secret_marker not in str(exc_info.value)
    assert secret_marker not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("values", "expected_code"),
    [
        (
            {
                "runtime_spec_revision_id": f"runtime-spec-{'a' * 64}",
                "canonical_payload": "not-json",
                "payload_digest": "a" * 64,
            },
            "runtime_spec_payload_invalid_json",
        ),
        (
            _runtime_spec_envelope('{"z":1, "a":2}'),
            "runtime_spec_payload_not_canonical",
        ),
        (
            {
                **_runtime_spec_envelope("{}"),
                "runtime_spec_revision_id": f"runtime-spec-{'0' * 64}",
                "payload_digest": "0" * 64,
            },
            "runtime_spec_payload_digest_mismatch",
        ),
        (
            {
                **_runtime_spec_envelope("{}"),
                "runtime_spec_revision_id": f"runtime-spec-{'0' * 64}",
            },
            "runtime_spec_revision_id_mismatch",
        ),
        (
            {
                "runtime_spec_revision_id": f"runtime-spec-{'g' * 64}",
                "canonical_payload": "{}",
                "payload_digest": "g" * 64,
            },
            "runtime_spec_payload_digest_invalid",
        ),
    ],
    ids=["not-json", "not-canonical", "wrong-digest", "wrong-id", "nonhex-digest"],
)
def test_runtime_spec_rejects_inconsistent_envelopes_with_stable_codes(
    values: dict[str, str],
    expected_code: str,
) -> None:
    _domain, runtime_spec = _domain_modules()

    with pytest.raises(ValidationError) as exc_info:
        runtime_spec.RuntimeSpecRevision(**values)

    assert expected_code in str(exc_info.value)
    assert values["canonical_payload"] not in str(exc_info.value)
    assert values["canonical_payload"] not in repr(exc_info.value)


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
    assert reference.model_config["hide_input_in_errors"] is True
    secret_marker = "fictional-secret-reference-value"
    with pytest.raises(ValidationError) as exc_info:
        domain.SecretReference(**reference.model_dump(), secret_value=secret_marker)
    assert secret_marker not in str(exc_info.value)
    assert secret_marker not in repr(exc_info.value)

    for field_name in ("credential", "content_hash", "secret_path", "host_path"):
        with pytest.raises(ValidationError):
            domain.SecretReference(**reference.model_dump(), **{field_name: "forbidden"})
    with pytest.raises(ValidationError):
        domain.SecretReference(**{**reference.model_dump(), "provider_id": "unknown-provider"})
