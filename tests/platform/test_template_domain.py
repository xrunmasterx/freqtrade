import hashlib
import importlib
import json

import pytest
from pydantic import ValidationError

from freqtrade.markets import MarketType, ProductType
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


def _runtime_spec_payload() -> dict[str, object]:
    return {
        "owner_ref": _owner_scope().model_dump(mode="json"),
        "instance_kind": "execution-worker",
        "catalog_revision_id": "catalog-revision-1",
        "market_scope": {
            "market_id": MarketType.DIGITAL_ASSET,
            "product_ids": (ProductType.SPOT,),
            "venue_ids": ("binance",),
            "instrument_keys": ("BTC.USDT:spot",),
        },
        "environment": "paper",
        "adapter_template_revision_id": "adapter-template-revision-1",
        "template_digest": "a" * 64,
        "image_policy_id": "signed-freqtrade-image",
        "command_policy_id": "freqtrade-supervisor-command",
        "mount_policy_ids": ("runtime-userdata",),
        "network_policy_id": "internal-runtime-network",
        "health_profile_id": "freqtrade-http-health",
        "resource_profile_id": "paper-probe-small",
        "state_layout_id": "freqtrade-userdata-v1",
        "state_allocation_id": "state-allocation-1",
        "secret_reference_ids": ("secret-reference-1",),
        "config_blob_commit": "1" * 40,
        "strategy_commit": "2" * 40,
        "safety_policy_commit": "3" * 40,
        "root_commit": "4" * 40,
        "backend_commit": "5" * 64,
        "frontend_commit": "6" * 40,
        "strategies_commit": "7" * 40,
        "config_blob_digest": "8" * 64,
        "strategy_digest": "9" * 64,
        "safety_policy_digest": "a" * 64,
    }


def _canonical_runtime_spec_payload() -> str:
    return json.dumps(
        _runtime_spec_payload(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def test_public_contracts_are_exported_with_closed_enum_values() -> None:
    domain, runtime_spec = _domain_modules()
    platform = importlib.import_module("freqtrade.platform")
    expected_exports = {
        "AdapterTemplate",
        "FrozenPlatformModel",
        "RuntimeMarketScope",
        "RuntimeSpecPayload",
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
    assert runtime_spec.RuntimeMarketScope is platform.RuntimeMarketScope
    assert runtime_spec.RuntimeSpecPayload is platform.RuntimeSpecPayload


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
    first_payload = _runtime_spec_payload()
    second_payload = dict(reversed(first_payload.items()))
    market_scope = first_payload["market_scope"]
    assert isinstance(market_scope, dict)
    second_payload["market_scope"] = dict(reversed(market_scope.items()))
    expected_json = json.dumps(
        first_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected_digest = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()

    first = runtime_spec.RuntimeSpecRevision.from_payload(first_payload)
    second = runtime_spec.RuntimeSpecRevision.from_payload(second_payload)
    first_payload["owner_ref"] = "later-mutation"

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


def test_runtime_spec_payload_is_exact_frozen_and_accepts_only_stable_references() -> None:
    _domain, runtime_spec = _domain_modules()
    payload = runtime_spec.RuntimeSpecPayload.model_validate(_runtime_spec_payload())

    assert set(type(payload).model_fields) == {
        *_runtime_spec_payload(),
        "strategy_class_name",
    }
    assert payload.model_config["frozen"] is True
    assert payload.model_config["extra"] == "forbid"
    assert payload.model_config["hide_input_in_errors"] is True
    assert payload.secret_reference_ids == ("secret-reference-1",)

    revision = runtime_spec.RuntimeSpecRevision.from_payload(payload)
    assert "secret-reference-1" in revision.canonical_payload


def test_runtime_spec_persists_strategy_class_and_preserves_legacy_digest() -> None:
    _domain, runtime_spec = _domain_modules()
    current_payload = {
        **_runtime_spec_payload(),
        "strategy_class_name": "SampleStrategy",
    }

    current = runtime_spec.RuntimeSpecRevision.from_payload(current_payload)
    assert json.loads(current.canonical_payload)["strategy_class_name"] == "SampleStrategy"

    legacy_payload = _runtime_spec_payload()
    legacy_canonical = json.dumps(
        legacy_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    legacy = runtime_spec.RuntimeSpecRevision(**_runtime_spec_envelope(legacy_canonical))
    decoded_legacy = runtime_spec.RuntimeSpecPayload.model_validate_json(legacy.canonical_payload)

    assert decoded_legacy.strategy_class_name is None
    assert legacy.canonical_payload == legacy_canonical
    assert legacy.payload_digest == hashlib.sha256(legacy_canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("strategy_class_name", ["bad-class-name", "1Strategy", "A.B"])
def test_runtime_spec_rejects_invalid_strategy_class_names(
    strategy_class_name: str,
) -> None:
    _domain, runtime_spec = _domain_modules()

    with pytest.raises(ValidationError):
        runtime_spec.RuntimeSpecPayload.model_validate(
            {
                **_runtime_spec_payload(),
                "strategy_class_name": strategy_class_name,
            }
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "config_blob_commit",
        "strategy_commit",
        "safety_policy_commit",
        "root_commit",
        "backend_commit",
        "frontend_commit",
        "strategies_commit",
    ],
)
@pytest.mark.parametrize("invalid_value", ["a" * 39, "A" * 40, "g" * 40])
def test_runtime_spec_payload_accepts_only_lowercase_git_object_ids(
    field_name: str,
    invalid_value: str,
) -> None:
    _domain, runtime_spec = _domain_modules()

    with pytest.raises(ValidationError):
        runtime_spec.RuntimeSpecPayload.model_validate(
            {**_runtime_spec_payload(), field_name: invalid_value}
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "template_digest",
        "config_blob_digest",
        "strategy_digest",
        "safety_policy_digest",
    ],
)
@pytest.mark.parametrize("invalid_value", ["a" * 63, "A" * 64, "g" * 64])
def test_runtime_spec_payload_accepts_only_lowercase_sha256_digests(
    field_name: str,
    invalid_value: str,
) -> None:
    _domain, runtime_spec = _domain_modules()

    with pytest.raises(ValidationError):
        runtime_spec.RuntimeSpecPayload.model_validate(
            {**_runtime_spec_payload(), field_name: invalid_value}
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("mount_policy_ids", ()),
        ("mount_policy_ids", ("runtime-userdata", "runtime-userdata")),
        ("secret_reference_ids", ("secret-reference-1", "secret-reference-1")),
    ],
)
def test_runtime_spec_payload_requires_unique_reference_tuples(
    field_name: str,
    invalid_value: tuple[str, ...],
) -> None:
    _domain, runtime_spec = _domain_modules()

    with pytest.raises(ValidationError):
        runtime_spec.RuntimeSpecPayload.model_validate(
            {**_runtime_spec_payload(), field_name: invalid_value}
        )


def test_runtime_market_scope_is_closed_and_preserves_opaque_instrument_keys() -> None:
    _domain, runtime_spec = _domain_modules()
    scope = runtime_spec.RuntimeMarketScope(
        market_id=MarketType.DIGITAL_ASSET,
        product_ids=(ProductType.SPOT,),
        venue_ids=("binance",),
        instrument_keys=("BTC.USDT:spot",),
    )

    assert set(type(scope).model_fields) == {
        "market_id",
        "product_ids",
        "venue_ids",
        "instrument_keys",
    }
    assert scope.instrument_keys == ("BTC.USDT:spot",)

    invalid_values = (
        {"product_ids": ()},
        {"product_ids": (ProductType.SPOT, ProductType.SPOT)},
        {"venue_ids": ("binance", "binance")},
        {"instrument_keys": ("BTC.USDT:spot", "BTC.USDT:spot")},
        {"instrument_keys": ("",)},
        {"instrument_keys": ("x" * 257,)},
        {"connector": "forbidden"},
    )
    base_values = scope.model_dump()
    for changes in invalid_values:
        with pytest.raises(ValidationError):
            runtime_spec.RuntimeMarketScope(**{**base_values, **changes})


@pytest.mark.parametrize(
    ("extra_key", "extra_value", "nested"),
    [
        ("client_secret", "fictional-client-secret", False),
        ("access_token", "fictional-access-token", False),
        ("refresh_token", "fictional-refresh-token", False),
        ("client_credentials", "fictional-client-credentials", False),
        ("CLIENT_SECRET", "fictional-uppercase-secret", False),
        ("client-secret", "fictional-hyphen-secret", False),
        (" client_secret ", "fictional-whitespace-secret", False),
        ("client_credentials", {"value": "fictional-nested-object-secret"}, True),
        ("refresh_token", ["fictional-nested-array-secret"], True),
    ],
    ids=[
        "client-secret",
        "access-token",
        "refresh-token",
        "client-credentials",
        "case-variant",
        "connector-variant",
        "whitespace-variant",
        "nested-object",
        "nested-array",
    ],
)
def test_runtime_spec_closed_payload_rejects_extra_keys_without_echoing_values(
    extra_key: str,
    extra_value: object,
    nested: bool,
) -> None:
    _domain, runtime_spec = _domain_modules()
    payload = _runtime_spec_payload()
    target = payload["market_scope"] if nested else payload
    assert isinstance(target, dict)
    target[extra_key] = extra_value

    with pytest.raises(ValidationError) as exc_info:
        runtime_spec.RuntimeSpecRevision.from_payload(payload)

    error_text = str(exc_info.value)
    error_repr = repr(exc_info.value)
    for marker in (
        "fictional-client-secret",
        "fictional-access-token",
        "fictional-refresh-token",
        "fictional-client-credentials",
        "fictional-uppercase-secret",
        "fictional-hyphen-secret",
        "fictional-whitespace-secret",
        "fictional-nested-object-secret",
        "fictional-nested-array-secret",
    ):
        assert marker not in error_text
        assert marker not in error_repr


def test_runtime_spec_direct_construction_cannot_bypass_closed_payload_boundary() -> None:
    _domain, runtime_spec = _domain_modules()
    secret_marker = "fictional-direct-runtime-secret"
    payload = _runtime_spec_payload()
    payload["access_token"] = {"nested": [secret_marker]}
    canonical_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    with pytest.raises(ValidationError) as exc_info:
        runtime_spec.RuntimeSpecRevision(**_runtime_spec_envelope(canonical_payload))

    assert "runtime_spec_payload_invalid" in str(exc_info.value)
    assert secret_marker not in str(exc_info.value)
    assert secret_marker not in repr(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    ["fictional-top-level-string", ["fictional-top-level-list"], 17, None],
    ids=["string", "list", "number", "null"],
)
def test_runtime_spec_direct_construction_rejects_non_object_payloads(payload: object) -> None:
    _domain, runtime_spec = _domain_modules()
    canonical_payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    with pytest.raises(ValidationError) as exc_info:
        runtime_spec.RuntimeSpecRevision(**_runtime_spec_envelope(canonical_payload))

    assert "runtime_spec_payload_not_object" in str(exc_info.value)
    assert "fictional-top-level" not in str(exc_info.value)
    assert "fictional-top-level" not in repr(exc_info.value)


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
            _runtime_spec_envelope(json.dumps(_runtime_spec_payload(), ensure_ascii=False)),
            "runtime_spec_payload_not_canonical",
        ),
        (
            {
                **_runtime_spec_envelope(_canonical_runtime_spec_payload()),
                "runtime_spec_revision_id": f"runtime-spec-{'0' * 64}",
                "payload_digest": "0" * 64,
            },
            "runtime_spec_payload_digest_mismatch",
        ),
        (
            {
                **_runtime_spec_envelope(_canonical_runtime_spec_payload()),
                "runtime_spec_revision_id": f"runtime-spec-{'0' * 64}",
            },
            "runtime_spec_revision_id_mismatch",
        ),
        (
            {
                "runtime_spec_revision_id": f"runtime-spec-{'g' * 64}",
                "canonical_payload": _canonical_runtime_spec_payload(),
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
