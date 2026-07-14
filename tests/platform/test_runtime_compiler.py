import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from freqtrade.markets import (
    CapabilityDecision,
    CapabilityName,
    CatalogStatus,
    MarketCatalog,
    MarketDefinition,
    MarketType,
    ProductCapabilityPolicy,
    ProductDefinition,
    ProductType,
    VenueDefinition,
)
from freqtrade.markets.default_catalog import CatalogSnapshot
from freqtrade.platform.runtime_compiler import (
    ClosedPolicySnapshot,
    CommittedConfigIdentity,
    CommittedSafetyPolicyIdentity,
    CommittedStrategyIdentity,
    CompileRuntimeRequest,
    ComponentCommits,
    RuntimeCompileError,
    RuntimeSpecCompiler,
)
from freqtrade.platform.runtime_domain import RuntimeOwnerKind, RuntimeOwnerRef
from freqtrade.platform.runtime_spec import RuntimeMarketScope, RuntimeSpecRevision
from freqtrade.platform.template_domain import (
    AdapterTemplate,
    SecretReference,
    SecretReferenceStatus,
    StateAllocation,
    StateAllocationKind,
    StateAllocationStatus,
    TemplateStatus,
)
from freqtrade.platform.template_repository import AdapterTemplateRevisionView


NOW = datetime(2026, 7, 14, 9, 30, tzinfo=UTC)
ROOT_COMMIT = "1" * 40
BACKEND_COMMIT = "2" * 40
FRONTEND_COMMIT = "3" * 40
STRATEGIES_COMMIT = "4" * 40
TEMPLATE_DIGEST = "a" * 64
TEMPLATE_REVISION_ID = f"template-{TEMPLATE_DIGEST}"


def _owner() -> RuntimeOwnerRef:
    return RuntimeOwnerRef(
        owner_kind=RuntimeOwnerKind.PAPER_PROBE,
        owner_id="phase2-spot-paper-probe",
        owner_revision="phase2-spot-paper-probe-v1",
    )


def _market_scope() -> RuntimeMarketScope:
    return RuntimeMarketScope(
        market_id=MarketType.DIGITAL_ASSET,
        product_ids=(ProductType.SPOT,),
        venue_ids=("bitget",),
        instrument_keys=(),
    )


def _catalog_snapshot(*, allow_live: bool = False) -> CatalogSnapshot:
    live_decision = (
        CapabilityDecision.allow()
        if allow_live
        else CapabilityDecision.deny("live_lane_not_enabled")
    )
    return CatalogSnapshot(
        revision_id="paper-probe-catalog-v1",
        catalog=MarketCatalog(
            markets=(
                MarketDefinition(
                    market_id=MarketType.DIGITAL_ASSET,
                    display_name="Digital Assets",
                    status=CatalogStatus.ACTIVE,
                ),
            ),
            products=(
                ProductDefinition(
                    market_id=MarketType.DIGITAL_ASSET,
                    product_id=ProductType.SPOT,
                    display_name="Spot",
                    status=CatalogStatus.ACTIVE,
                ),
                ProductDefinition(
                    market_id=MarketType.DIGITAL_ASSET,
                    product_id=ProductType.PERPETUAL,
                    display_name="Perpetual",
                    status=CatalogStatus.ACTIVE,
                ),
            ),
            venues=(
                VenueDefinition(
                    venue_id="bitget",
                    market_id=MarketType.DIGITAL_ASSET,
                    display_name="Bitget",
                    status=CatalogStatus.ACTIVE,
                    product_ids=(ProductType.SPOT,),
                ),
                VenueDefinition(
                    venue_id="other-venue",
                    market_id=MarketType.DIGITAL_ASSET,
                    display_name="Other Venue",
                    status=CatalogStatus.ACTIVE,
                    product_ids=(ProductType.SPOT, ProductType.PERPETUAL),
                ),
            ),
        ),
        product_policies=(
            ProductCapabilityPolicy(
                market_id=MarketType.DIGITAL_ASSET,
                product_id=ProductType.SPOT,
                decisions={
                    CapabilityName.RESEARCH: CapabilityDecision.allow(),
                    CapabilityName.PAPER_TRADING: CapabilityDecision.allow(),
                    CapabilityName.LIVE_TRADING: live_decision,
                },
            ),
            ProductCapabilityPolicy(
                market_id=MarketType.DIGITAL_ASSET,
                product_id=ProductType.PERPETUAL,
                decisions={
                    CapabilityName.RESEARCH: CapabilityDecision.allow(),
                    CapabilityName.PAPER_TRADING: CapabilityDecision.allow(),
                    CapabilityName.LIVE_TRADING: live_decision,
                },
            ),
        ),
    )


def _template_revision() -> AdapterTemplateRevisionView:
    return AdapterTemplateRevisionView(
        revision_id=TEMPLATE_REVISION_ID,
        template=AdapterTemplate(
            template_id="freqtrade-paper-probe-v1",
            semantic_version="1.0.0",
            allowed_instance_kinds=("freqtrade",),
            allowed_owner_kinds=(RuntimeOwnerKind.PAPER_PROBE,),
            allowed_environments=("paper",),
            image_policy_id="freqtrade-reviewed-image-v1",
            command_policy_id="freqtrade-spot-paper-v1",
            mount_policy_ids=(
                "runtime-config-ro-v1",
                "strategy-ro-v1",
                "managed-state-rw-v1",
                "api-secrets-ro-v1",
            ),
            network_policy_id="isolated-public-market-data-v1",
            health_profile_id="freqtrade-ping-v1",
            resource_profile_id="freqtrade-small-v1",
            secret_classes=("api_password", "jwt_secret", "ws_token"),
            state_layout_id="freqtrade-state-v1",
        ),
        payload_digest=TEMPLATE_DIGEST,
        source_commit=ROOT_COMMIT,
        root_commit=ROOT_COMMIT,
        backend_commit=BACKEND_COMMIT,
        frontend_commit=FRONTEND_COMMIT,
        strategies_commit=STRATEGIES_COMMIT,
        status=TemplateStatus.ACTIVE,
        published_by="platform-admin",
        published_at=NOW,
        deprecated_at=None,
        revoked_at=None,
    )


def _revision_for_template(template: AdapterTemplate) -> AdapterTemplateRevisionView:
    canonical_payload = json.dumps(
        {"schema_version": 1, **template.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"
    digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    base_revision = _template_revision()
    return AdapterTemplateRevisionView(
        **{
            **base_revision.model_dump(mode="python"),
            "revision_id": f"template-{digest}",
            "template": template,
            "payload_digest": digest,
        }
    )


def _state_allocation() -> StateAllocation:
    return StateAllocation(
        state_allocation_id="state-paper-probe-v1",
        instance_id="phase2-spot-paper-probe",
        layout_id="freqtrade-state-v1",
        provider_id="managed-local-v1",
        kind=StateAllocationKind.FRESH,
        status=StateAllocationStatus.RESERVED,
        generation=1,
        restore_source_bundle_id=None,
    )


def _secret_references() -> tuple[SecretReference, ...]:
    return tuple(
        SecretReference(
            secret_reference_id=reference_id,
            provider_id="local-file-v1",
            secret_class=secret_class,
            logical_name=f"paper-probe-{secret_class}",
            owner_scope=_owner(),
            status=SecretReferenceStatus.ACTIVE,
        )
        for reference_id, secret_class in (
            ("secret-ws-token", "ws_token"),
            ("secret-api-password", "api_password"),
            ("secret-jwt-secret", "jwt_secret"),
        )
    )


def _closed_policies() -> ClosedPolicySnapshot:
    return ClosedPolicySnapshot(
        image_policy_ids=frozenset({"freqtrade-reviewed-image-v1"}),
        command_policy_ids=frozenset({"freqtrade-spot-paper-v1"}),
        mount_policy_ids=frozenset(
            {
                "runtime-config-ro-v1",
                "strategy-ro-v1",
                "managed-state-rw-v1",
                "api-secrets-ro-v1",
            }
        ),
        network_policy_ids=frozenset({"isolated-public-market-data-v1"}),
        health_profile_ids=frozenset({"freqtrade-ping-v1"}),
        resource_profile_ids=frozenset({"freqtrade-small-v1"}),
        state_layout_ids=frozenset({"freqtrade-state-v1"}),
        source_commit=ROOT_COMMIT,
    )


def _request_values() -> dict[str, object]:
    scope = _market_scope()
    return {
        "owner_ref": _owner().model_dump(mode="json"),
        "instance_id": "phase2-spot-paper-probe",
        "instance_kind": "freqtrade",
        "catalog_revision_id": "paper-probe-catalog-v1",
        "market_scope": scope.model_dump(mode="json"),
        "environment": "paper",
        "adapter_template_revision_id": TEMPLATE_REVISION_ID,
        "state_allocation_id": "state-paper-probe-v1",
        "secret_reference_ids": (
            "secret-ws-token",
            "secret-api-password",
            "secret-jwt-secret",
        ),
        "config_identity": {
            "commit": ROOT_COMMIT,
            "digest": "b" * 64,
            "market_scope": scope.model_dump(mode="json"),
            "dry_run": True,
        },
        "strategy_identity": {
            "commit": STRATEGIES_COMMIT,
            "digest": "c" * 64,
            "strategy_class_name": "SampleStrategy",
        },
        "safety_policy_identity": {
            "commit": ROOT_COMMIT,
            "digest": "d" * 64,
            "dry_run": True,
        },
        "component_commits": {
            "root_commit": ROOT_COMMIT,
            "backend_commit": BACKEND_COMMIT,
            "frontend_commit": FRONTEND_COMMIT,
            "strategies_commit": STRATEGIES_COMMIT,
        },
    }


def _request(**changes: object) -> CompileRuntimeRequest:
    return CompileRuntimeRequest.model_validate({**_request_values(), **changes})


def _compiler(
    *,
    catalog_snapshot: CatalogSnapshot | None = None,
    template_revision: AdapterTemplateRevisionView | None = None,
    state_allocation: StateAllocation | None = None,
    secret_references: tuple[SecretReference, ...] | None = None,
    closed_policy_snapshot: ClosedPolicySnapshot | None = None,
) -> RuntimeSpecCompiler:
    return RuntimeSpecCompiler(
        catalog_snapshot=catalog_snapshot or _catalog_snapshot(),
        template_revision=template_revision or _template_revision(),
        state_allocation=state_allocation or _state_allocation(),
        secret_references=secret_references or _secret_references(),
        closed_policy_snapshot=closed_policy_snapshot or _closed_policies(),
    )


def _replace_nested(values: dict[str, object], dotted_path: str, value: object) -> None:
    path = dotted_path.split(".")
    target = values
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value


def test_compile_request_and_identity_models_are_exact_frozen_closed_schemas() -> None:
    request = _request()

    assert set(type(request).model_fields) == set(_request_values())
    assert set(CommittedConfigIdentity.model_fields) == {
        "commit",
        "digest",
        "market_scope",
        "dry_run",
    }
    assert set(CommittedStrategyIdentity.model_fields) == {
        "commit",
        "digest",
        "strategy_class_name",
    }
    assert set(CommittedSafetyPolicyIdentity.model_fields) == {
        "commit",
        "digest",
        "dry_run",
    }
    assert set(ComponentCommits.model_fields) == {
        "root_commit",
        "backend_commit",
        "frontend_commit",
        "strategies_commit",
    }
    assert request.model_config["frozen"] is True
    assert request.model_config["extra"] == "forbid"
    assert request.model_config["hide_input_in_errors"] is True


def test_paper_probe_compiles_exact_bound_runtime_spec() -> None:
    revision = _compiler().compile(_request())
    payload = json.loads(revision.canonical_payload)

    assert payload["owner_ref"] == {
        "owner_kind": "paper_probe",
        "owner_id": "phase2-spot-paper-probe",
        "owner_revision": "phase2-spot-paper-probe-v1",
    }
    assert payload["market_scope"] == {
        "market_id": "digital_asset",
        "product_ids": ["spot"],
        "venue_ids": ["bitget"],
        "instrument_keys": [],
    }
    assert payload["environment"] == "paper"
    assert payload["state_allocation_id"] == "state-paper-probe-v1"
    assert payload["secret_reference_ids"] == [
        "secret-api-password",
        "secret-jwt-secret",
        "secret-ws-token",
    ]
    assert payload["image_policy_id"] == "freqtrade-reviewed-image-v1"
    assert payload["root_commit"] == ROOT_COMMIT
    assert isinstance(revision.canonical_payload, str)


def test_compile_has_literal_golden_canonical_json_and_digest() -> None:
    revision = _compiler().compile(_request())
    expected_payload = (
        '{"adapter_template_revision_id":"template-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"backend_commit":"2222222222222222222222222222222222222222",'
        '"catalog_revision_id":"paper-probe-catalog-v1",'
        '"command_policy_id":"freqtrade-spot-paper-v1",'
        '"config_blob_commit":"1111111111111111111111111111111111111111",'
        '"config_blob_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"environment":"paper",'
        '"frontend_commit":"3333333333333333333333333333333333333333",'
        '"health_profile_id":"freqtrade-ping-v1",'
        '"image_policy_id":"freqtrade-reviewed-image-v1",'
        '"instance_kind":"freqtrade",'
        '"market_scope":{"instrument_keys":[],"market_id":"digital_asset","product_ids":["spot"],"venue_ids":["bitget"]},'
        '"mount_policy_ids":["runtime-config-ro-v1","strategy-ro-v1","managed-state-rw-v1","api-secrets-ro-v1"],'
        '"network_policy_id":"isolated-public-market-data-v1",'
        '"owner_ref":{"owner_id":"phase2-spot-paper-probe","owner_kind":"paper_probe","owner_revision":"phase2-spot-paper-probe-v1"},'
        '"resource_profile_id":"freqtrade-small-v1",'
        '"root_commit":"1111111111111111111111111111111111111111",'
        '"safety_policy_commit":"1111111111111111111111111111111111111111",'
        '"safety_policy_digest":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
        '"secret_reference_ids":["secret-api-password","secret-jwt-secret","secret-ws-token"],'
        '"state_allocation_id":"state-paper-probe-v1",'
        '"state_layout_id":"freqtrade-state-v1",'
        '"strategies_commit":"4444444444444444444444444444444444444444",'
        '"strategy_commit":"4444444444444444444444444444444444444444",'
        '"strategy_digest":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        '"template_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
    )

    assert revision.canonical_payload == expected_payload
    assert revision.payload_digest == (
        "5224b382ab312fe4a328ea49d65d641914cceb543c7f673b32fae183ec7bb45e"
    )
    assert revision.runtime_spec_revision_id == f"runtime-spec-{revision.payload_digest}"


def test_reordered_secret_inputs_compile_to_identical_revision() -> None:
    first = _compiler().compile(_request())
    reordered_values = _request_values()
    reordered_values["secret_reference_ids"] = tuple(
        reversed(reordered_values["secret_reference_ids"])
    )
    second = _compiler(secret_references=tuple(reversed(_secret_references()))).compile(
        reordered_values
    )

    assert second == first


@pytest.mark.parametrize(
    "field_name",
    [
        "image",
        "raw_image",
        "entrypoint",
        "command",
        "argument",
        "arguments",
        "args",
        "environment_passthrough",
        "environment_variables",
        "mount",
        "mounts",
        "path",
        "host_path",
        "port",
        "host_port",
        "hostname",
        "network",
        "project",
        "service",
        "container",
        "device",
        "capability",
        "capabilities",
        "privilege",
        "privileged",
        "compose",
        "compose_fragment",
        "secret_value",
        "secret_version",
        "secret_path",
        "policy_id",
    ],
)
@pytest.mark.parametrize(
    "target_path",
    [
        None,
        "owner_ref",
        "market_scope",
        "config_identity",
        "config_identity.market_scope",
        "strategy_identity",
        "safety_policy_identity",
        "component_commits",
    ],
)
def test_forbidden_raw_power_is_rejected_without_echo(
    field_name: str,
    target_path: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _request_values()
    marker = f"fictional-sensitive-marker-{field_name}"
    if target_path is None:
        values[field_name] = {"nested": [marker]}
    else:
        _replace_nested(values, f"{target_path}.{field_name}", {"nested": [marker]})
    canonicalization_calls = 0

    def record_canonicalization(_cls: type[RuntimeSpecRevision], _payload: object) -> None:
        nonlocal canonicalization_calls
        canonicalization_calls += 1

    monkeypatch.setattr(RuntimeSpecRevision, "from_payload", classmethod(record_canonicalization))

    with pytest.raises(RuntimeCompileError, match=r"^runtime_request_invalid$") as exc_info:
        _compiler().compile(values)

    assert canonicalization_calls == 0
    assert marker not in str(exc_info.value)
    assert marker not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("dotted_path", "invalid_value"),
    [
        ("config_identity.commit", "A" * 40),
        ("config_identity.digest", "b" * 63),
        ("config_identity.dry_run", 1),
        ("strategy_identity.commit", "4" * 39),
        ("strategy_identity.digest", "C" * 64),
        ("strategy_identity.strategy_class_name", "bad-class-name"),
        ("safety_policy_identity.dry_run", "true"),
        ("component_commits.backend_commit", "B" * 40),
    ],
)
def test_invalid_committed_identities_fail_at_closed_request_boundary(
    dotted_path: str,
    invalid_value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _request_values()
    _replace_nested(values, dotted_path, invalid_value)

    def fail_canonicalization(_cls: type[RuntimeSpecRevision], _payload: object) -> None:
        raise AssertionError("invalid request reached canonicalization")

    monkeypatch.setattr(RuntimeSpecRevision, "from_payload", classmethod(fail_canonicalization))
    with pytest.raises(RuntimeCompileError, match=r"^runtime_request_invalid$"):
        _compiler().compile(values)


def _assert_compile_error_before_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
    compiler: RuntimeSpecCompiler,
    request: CompileRuntimeRequest | dict[str, object],
    expected_code: str,
) -> None:
    def fail_canonicalization(_cls: type[RuntimeSpecRevision], _payload: object) -> None:
        raise AssertionError("canonicalization reached after failed validation")

    monkeypatch.setattr(RuntimeSpecRevision, "from_payload", classmethod(fail_canonicalization))
    with pytest.raises(RuntimeCompileError, match=rf"^{expected_code}$"):
        compiler.compile(request)


def test_validation_order_is_stable_and_stops_before_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_owner = RuntimeOwnerRef(
        owner_kind=RuntimeOwnerKind.PAPER_PROBE,
        owner_id="another-probe",
        owner_revision="another-probe-v1",
    )
    request_values = _request_values()
    request_values["owner_ref"] = wrong_owner.model_dump(mode="json")
    request_values["catalog_revision_id"] = "wrong-catalog"
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(),
        CompileRuntimeRequest.model_validate(request_values),
        "runtime_owner_invalid",
    )

    live_values = _request_values()
    live_values["environment"] = "live"
    deprecated = _template_revision().model_copy(
        update={
            "status": TemplateStatus.DEPRECATED,
            "deprecated_at": NOW,
        }
    )
    catalog_first_values = {**live_values, "catalog_revision_id": "wrong-catalog"}
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(
            catalog_snapshot=_catalog_snapshot(allow_live=True),
            template_revision=deprecated,
        ),
        CompileRuntimeRequest.model_validate(catalog_first_values),
        "runtime_catalog_invalid",
    )
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(
            catalog_snapshot=_catalog_snapshot(allow_live=True),
            template_revision=deprecated,
        ),
        CompileRuntimeRequest.model_validate(live_values),
        "runtime_environment_invalid",
    )

    ready_state = _state_allocation().model_copy(
        update={"status": StateAllocationStatus.READY}
    )
    disabled_secrets = (
        _secret_references()[0].model_copy(update={"status": SecretReferenceStatus.DISABLED}),
        *_secret_references()[1:],
    )
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(template_revision=deprecated, state_allocation=ready_state),
        _request(),
        "runtime_template_invalid",
    )
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(state_allocation=ready_state, secret_references=disabled_secrets),
        _request(),
        "runtime_state_invalid",
    )

    artifact_values = _request_values()
    config_identity = artifact_values["config_identity"]
    assert isinstance(config_identity, dict)
    config_identity["commit"] = "5" * 40
    missing_policy = _closed_policies().model_copy(update={"image_policy_ids": frozenset()})
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(
            secret_references=disabled_secrets,
            closed_policy_snapshot=missing_policy,
        ),
        CompileRuntimeRequest.model_validate(artifact_values),
        "runtime_secrets_invalid",
    )
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(closed_policy_snapshot=missing_policy),
        CompileRuntimeRequest.model_validate(artifact_values),
        "runtime_artifacts_invalid",
    )

    provenance_values = _request_values()
    component_commits = provenance_values["component_commits"]
    assert isinstance(component_commits, dict)
    component_commits["backend_commit"] = "5" * 40
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(closed_policy_snapshot=missing_policy),
        CompileRuntimeRequest.model_validate(provenance_values),
        "runtime_policies_invalid",
    )


@pytest.mark.parametrize(
    ("compiler_factory", "request_factory", "expected_code"),
    [
        (
            lambda: _compiler(),
            lambda: _request(catalog_revision_id="another-catalog"),
            "runtime_catalog_invalid",
        ),
        (
            lambda: _compiler(
                template_revision=_template_revision().model_copy(
                    update={"status": TemplateStatus.REVOKED, "revoked_at": NOW}
                )
            ),
            _request,
            "runtime_template_invalid",
        ),
        (
            lambda: _compiler(
                state_allocation=_state_allocation().model_copy(
                    update={"status": StateAllocationStatus.READY}
                )
            ),
            _request,
            "runtime_state_invalid",
        ),
        (
            lambda: _compiler(
                secret_references=(
                    _secret_references()[0].model_copy(
                        update={"status": SecretReferenceStatus.DISABLED}
                    ),
                    *_secret_references()[1:],
                )
            ),
            _request,
            "runtime_secrets_invalid",
        ),
        (
            lambda: _compiler(),
            lambda: _request(
                config_identity={
                    **_request_values()["config_identity"],
                    "commit": "5" * 40,
                }
            ),
            "runtime_artifacts_invalid",
        ),
        (
            lambda: _compiler(
                closed_policy_snapshot=_closed_policies().model_copy(
                    update={"image_policy_ids": frozenset()}
                )
            ),
            _request,
            "runtime_policies_invalid",
        ),
        (
            lambda: _compiler(),
            lambda: _request(
                component_commits={
                    **_request_values()["component_commits"],
                    "backend_commit": "5" * 40,
                }
            ),
            "runtime_provenance_invalid",
        ),
    ],
    ids=["catalog", "template", "state", "secrets", "artifacts", "policies", "provenance"],
)
def test_each_validation_stage_has_a_stable_detail_free_error(
    compiler_factory: Callable[[], RuntimeSpecCompiler],
    request_factory: Callable[[], CompileRuntimeRequest],
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        compiler_factory(),
        request_factory(),
        expected_code,
    )


@pytest.mark.parametrize(
    ("context_name", "context_value", "expected_code"),
    [
        (
            "catalog_snapshot",
            _catalog_snapshot().model_copy(
                update={
                    "product_policies": (
                        *_catalog_snapshot().product_policies,
                        _catalog_snapshot().product_policies[0],
                    )
                }
            ),
            "runtime_catalog_invalid",
        ),
        (
            "template_revision",
            _template_revision().model_copy(update={"payload_digest": "A" * 64}),
            "runtime_template_invalid",
        ),
        (
            "state_allocation",
            _state_allocation().model_copy(update={"generation": 0}),
            "runtime_state_invalid",
        ),
        (
            "secret_references",
            (
                _secret_references()[0].model_copy(update={"provider_id": "untrusted"}),
                *_secret_references()[1:],
            ),
            "runtime_secrets_invalid",
        ),
        (
            "closed_policy_snapshot",
            _closed_policies().model_copy(update={"source_commit": "A" * 40}),
            "runtime_policies_invalid",
        ),
    ],
)
def test_bound_typed_snapshots_are_revalidated_as_untrusted_data(
    context_name: str,
    context_value: object,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(**{context_name: context_value}),
        _request(),
        expected_code,
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"market_scope.market_id": "a_share"}, "runtime_catalog_invalid"),
        ({"market_scope.product_ids": ["spot", "perpetual"]}, "runtime_catalog_invalid"),
        ({"market_scope.venue_ids": ["other-venue"]}, "runtime_catalog_invalid"),
        ({"market_scope.instrument_keys": ["BTC/USDT"]}, "runtime_catalog_invalid"),
        (
            {"strategy_identity.strategy_class_name": "OtherStrategy"},
            "runtime_artifacts_invalid",
        ),
        ({"config_identity.dry_run": False}, "runtime_artifacts_invalid"),
        ({"safety_policy_identity.dry_run": False}, "runtime_artifacts_invalid"),
    ],
)
def test_paper_probe_fixed_gate_rejects_request_mutations(
    mutation: dict[str, object],
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _request_values()
    for dotted_path, value in mutation.items():
        _replace_nested(values, dotted_path, value)
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(),
        CompileRuntimeRequest.model_validate(values),
        expected_code,
    )


def test_paper_probe_rejects_live_before_template_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _request_values()
    values["environment"] = "live"
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(),
        CompileRuntimeRequest.model_validate(values),
        "runtime_catalog_invalid",
    )


def test_paper_probe_rejects_wrong_template_and_restored_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_template = _template_revision().model_copy(
        update={
            "template": _template_revision().template.model_copy(
                update={"template_id": "other-template"}
            )
        }
    )
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(template_revision=wrong_template),
        _request(),
        "runtime_template_invalid",
    )

    restored = _state_allocation().model_copy(
        update={
            "kind": StateAllocationKind.RESTORED,
            "restore_source_bundle_id": "restore-bundle-v1",
        }
    )
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(state_allocation=restored),
        _request(),
        "runtime_state_invalid",
    )


def test_paper_probe_instance_kind_is_fixed_independently_of_template_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expanded_template = _template_revision().template.model_copy(
        update={"allowed_instance_kinds": ("freqtrade", "added-kind")}
    )
    expanded_revision = _revision_for_template(expanded_template)
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(template_revision=expanded_revision),
        _request(
            instance_kind="added-kind",
            adapter_template_revision_id=expanded_revision.revision_id,
        ),
        "runtime_template_invalid",
    )


def test_paper_probe_secret_classes_are_fixed_independently_of_template_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expanded_template = _template_revision().template.model_copy(
        update={
            "secret_classes": (
                *_template_revision().template.secret_classes,
                "extra_class",
            )
        }
    )
    expanded_revision = _revision_for_template(expanded_template)
    extra_reference = SecretReference(
        secret_reference_id="secret-extra-class",
        provider_id="local-file-v1",
        secret_class="extra_class",
        logical_name="paper-probe-extra-class",
        owner_scope=_owner(),
        status=SecretReferenceStatus.ACTIVE,
    )
    values = _request_values()
    values["adapter_template_revision_id"] = expanded_revision.revision_id
    values["secret_reference_ids"] = (*values["secret_reference_ids"], "secret-extra-class")
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(
            template_revision=expanded_revision,
            secret_references=(*_secret_references(), extra_reference),
        ),
        CompileRuntimeRequest.model_validate(values),
        "runtime_secrets_invalid",
    )


def test_paper_probe_rejects_extra_secret_class_and_capability_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra_reference = SecretReference(
        secret_reference_id="secret-extra",
        provider_id="local-file-v1",
        secret_class="extra_class",
        logical_name="extra-secret",
        owner_scope=_owner(),
        status=SecretReferenceStatus.ACTIVE,
    )
    values = _request_values()
    values["secret_reference_ids"] = (*values["secret_reference_ids"], "secret-extra")
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(secret_references=(*_secret_references(), extra_reference)),
        CompileRuntimeRequest.model_validate(values),
        "runtime_secrets_invalid",
    )

    denied_catalog = _catalog_snapshot().model_copy(
        update={
            "product_policies": tuple(
                policy.model_copy(update={"decisions": {}})
                for policy in _catalog_snapshot().product_policies
            )
        }
    )
    _assert_compile_error_before_canonicalization(
        monkeypatch,
        _compiler(catalog_snapshot=denied_catalog),
        _request(),
        "runtime_catalog_invalid",
    )
