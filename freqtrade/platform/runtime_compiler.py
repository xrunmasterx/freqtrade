from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, TypeVar

from pydantic import BaseModel, Field, StrictBool, ValidationError, field_validator

from freqtrade.markets.capability_policy import CapabilityName
from freqtrade.markets.catalog import CatalogStatus
from freqtrade.markets.default_catalog import CatalogSnapshot
from freqtrade.platform.runtime_domain import Identifier, RuntimeOwnerKind, RuntimeOwnerRef
from freqtrade.platform.runtime_spec import (
    RuntimeMarketScope,
    RuntimeSpecPayload,
    RuntimeSpecRevision,
)
from freqtrade.platform.template_domain import (
    FrozenPlatformModel,
    SecretReference,
    SecretReferenceStatus,
    StateAllocation,
    StateAllocationKind,
    StateAllocationStatus,
    TemplateStatus,
)


if TYPE_CHECKING:
    from freqtrade.platform.template_repository import AdapterTemplateRevisionView


_LowercaseSha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_GitObjectId = Annotated[str, Field(pattern=r"^([0-9a-f]{40}|[0-9a-f]{64})$")]
_StrategyClassName = Annotated[
    str,
    Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"),
]
_ModelT = TypeVar("_ModelT", bound=BaseModel)

_PAPER_PROBE_OWNER = RuntimeOwnerRef(
    owner_kind=RuntimeOwnerKind.PAPER_PROBE,
    owner_id="phase2-spot-paper-probe",
    owner_revision="phase2-spot-paper-probe-v1",
)
_PAPER_PROBE_SECRET_CLASSES = frozenset({"api_password", "jwt_secret", "ws_token"})


class CommittedConfigIdentity(FrozenPlatformModel):
    commit: _GitObjectId
    digest: _LowercaseSha256Digest
    market_scope: RuntimeMarketScope
    dry_run: StrictBool


class CommittedStrategyIdentity(FrozenPlatformModel):
    commit: _GitObjectId
    digest: _LowercaseSha256Digest
    strategy_class_name: _StrategyClassName


class CommittedSafetyPolicyIdentity(FrozenPlatformModel):
    commit: _GitObjectId
    digest: _LowercaseSha256Digest
    dry_run: StrictBool


class ComponentCommits(FrozenPlatformModel):
    root_commit: _GitObjectId
    backend_commit: _GitObjectId
    frontend_commit: _GitObjectId
    strategies_commit: _GitObjectId


class ClosedPolicySnapshot(FrozenPlatformModel):
    image_policy_ids: frozenset[Identifier] = Field(min_length=1)
    command_policy_ids: frozenset[Identifier] = Field(min_length=1)
    mount_policy_ids: frozenset[Identifier] = Field(min_length=1)
    network_policy_ids: frozenset[Identifier] = Field(min_length=1)
    health_profile_ids: frozenset[Identifier] = Field(min_length=1)
    resource_profile_ids: frozenset[Identifier] = Field(min_length=1)
    state_layout_ids: frozenset[Identifier] = Field(min_length=1)
    source_commit: _GitObjectId


class CompileRuntimeRequest(FrozenPlatformModel):
    owner_ref: RuntimeOwnerRef
    instance_id: Identifier
    instance_kind: Identifier
    catalog_revision_id: Identifier
    market_scope: RuntimeMarketScope
    environment: Literal["paper", "live"]
    adapter_template_revision_id: Identifier
    state_allocation_id: Identifier
    secret_reference_ids: tuple[Identifier, ...] = Field(min_length=1)
    config_identity: CommittedConfigIdentity
    strategy_identity: CommittedStrategyIdentity
    safety_policy_identity: CommittedSafetyPolicyIdentity
    component_commits: ComponentCommits

    @field_validator("secret_reference_ids")
    @classmethod
    def require_unique_secret_references(
        cls,
        value: tuple[Identifier, ...],
    ) -> tuple[Identifier, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate secret reference IDs are not allowed")
        return value


class RuntimeCompileError(RuntimeError):
    pass


def _public_model_data(model: BaseModel) -> dict[str, object]:
    return model.model_dump(
        mode="python",
        include=set(type(model).model_fields),
        warnings=False,
    )


def _revalidate_model(model_type: type[_ModelT], value: _ModelT) -> _ModelT:
    return model_type.model_validate(_public_model_data(value))


class RuntimeSpecCompiler:
    def __init__(
        self,
        *,
        catalog_snapshot: CatalogSnapshot,
        template_revision: AdapterTemplateRevisionView,
        state_allocation: StateAllocation,
        secret_references: tuple[SecretReference, ...],
        closed_policy_snapshot: ClosedPolicySnapshot,
    ) -> None:
        self._catalog_snapshot = catalog_snapshot
        self._template_revision = template_revision
        self._state_allocation = state_allocation
        self._secret_references = secret_references
        self._closed_policy_snapshot = closed_policy_snapshot

    def compile(self, request: object) -> RuntimeSpecRevision:
        validated_request = self._parse_request(request)
        self._validate_owner(validated_request)
        catalog = self._validate_catalog(validated_request)
        self._validate_environment(validated_request)
        template_revision = self._validate_template(validated_request)
        state_allocation = self._validate_state(validated_request, template_revision)
        secret_reference_ids = self._validate_secrets(
            validated_request,
            template_revision,
        )
        self._validate_artifacts(validated_request)
        policies = self._validate_policies(template_revision)
        self._validate_provenance(validated_request, template_revision, policies)
        return RuntimeSpecRevision.from_payload(
            self._build_payload(
                validated_request,
                catalog.revision_id,
                template_revision,
                state_allocation,
                secret_reference_ids,
            )
        )

    @staticmethod
    def _parse_request(request: object) -> CompileRuntimeRequest:
        try:
            if isinstance(request, BaseModel):
                request = _public_model_data(request)
            return CompileRuntimeRequest.model_validate(request)
        except (TypeError, ValueError, ValidationError):
            raise RuntimeCompileError("runtime_request_invalid") from None

    @staticmethod
    def _validate_owner(request: CompileRuntimeRequest) -> None:
        if (
            request.owner_ref.owner_kind is RuntimeOwnerKind.PAPER_PROBE
            and request.owner_ref != _PAPER_PROBE_OWNER
        ):
            raise RuntimeCompileError("runtime_owner_invalid")

    def _validate_catalog(self, request: CompileRuntimeRequest) -> CatalogSnapshot:
        try:
            catalog = _revalidate_model(CatalogSnapshot, self._catalog_snapshot)
        except (TypeError, ValueError, ValidationError):
            raise RuntimeCompileError("runtime_catalog_invalid") from None

        if catalog.revision_id != request.catalog_revision_id:
            raise RuntimeCompileError("runtime_catalog_invalid")

        market = next(
            (
                item
                for item in catalog.catalog.markets
                if item.market_id == request.market_scope.market_id
            ),
            None,
        )
        if market is None or market.status is not CatalogStatus.ACTIVE:
            raise RuntimeCompileError("runtime_catalog_invalid")

        requested_products = set(request.market_scope.product_ids)
        active_products = {
            item.product_id
            for item in catalog.catalog.products
            if item.market_id == request.market_scope.market_id
            and item.status is CatalogStatus.ACTIVE
        }
        if not requested_products <= active_products:
            raise RuntimeCompileError("runtime_catalog_invalid")

        for venue_id in request.market_scope.venue_ids:
            venue = next(
                (item for item in catalog.catalog.venues if item.venue_id == venue_id),
                None,
            )
            if (
                venue is None
                or venue.market_id != request.market_scope.market_id
                or venue.status is not CatalogStatus.ACTIVE
                or not requested_products <= set(venue.product_ids)
            ):
                raise RuntimeCompileError("runtime_catalog_invalid")

        capability = self._required_capability(request)
        for product_id in request.market_scope.product_ids:
            if not catalog.capability(
                request.market_scope.market_id,
                product_id,
                capability,
            ).allowed:
                raise RuntimeCompileError("runtime_catalog_invalid")

        paper_probe_scope = RuntimeMarketScope(
            market_id="digital_asset",
            product_ids=("spot",),
            venue_ids=("bitget",),
            instrument_keys=(),
        )
        if (
            request.owner_ref.owner_kind is RuntimeOwnerKind.PAPER_PROBE
            and request.market_scope != paper_probe_scope
        ):
            raise RuntimeCompileError("runtime_catalog_invalid")
        return catalog

    @staticmethod
    def _required_capability(request: CompileRuntimeRequest) -> CapabilityName:
        if request.owner_ref.owner_kind is RuntimeOwnerKind.WORKSPACE_WORKER:
            return CapabilityName.RESEARCH
        if request.environment == "paper":
            return CapabilityName.PAPER_TRADING
        return CapabilityName.LIVE_TRADING

    @staticmethod
    def _validate_environment(request: CompileRuntimeRequest) -> None:
        if (
            request.owner_ref.owner_kind is RuntimeOwnerKind.PAPER_PROBE
            and request.environment != "paper"
        ):
            raise RuntimeCompileError("runtime_environment_invalid")

    def _validate_template(
        self,
        request: CompileRuntimeRequest,
    ) -> AdapterTemplateRevisionView:
        try:
            revision = _revalidate_model(
                type(self._template_revision),
                self._template_revision,
            )
        except (TypeError, ValueError, ValidationError):
            raise RuntimeCompileError("runtime_template_invalid") from None

        template = revision.template
        if (
            revision.status is not TemplateStatus.ACTIVE
            or request.adapter_template_revision_id != revision.revision_id
            or request.owner_ref.owner_kind not in template.allowed_owner_kinds
            or request.instance_kind not in template.allowed_instance_kinds
            or request.environment not in template.allowed_environments
        ):
            raise RuntimeCompileError("runtime_template_invalid")
        if (
            request.owner_ref.owner_kind is RuntimeOwnerKind.PAPER_PROBE
            and (
                template.template_id != "freqtrade-paper-probe-v1"
                or request.instance_kind != "freqtrade"
            )
        ):
            raise RuntimeCompileError("runtime_template_invalid")
        return revision

    def _validate_state(
        self,
        request: CompileRuntimeRequest,
        template_revision: AdapterTemplateRevisionView,
    ) -> StateAllocation:
        try:
            allocation = _revalidate_model(StateAllocation, self._state_allocation)
        except (TypeError, ValueError, ValidationError):
            raise RuntimeCompileError("runtime_state_invalid") from None

        has_restore_source = allocation.restore_source_bundle_id is not None
        kind_matches_restore_source = (
            allocation.kind is StateAllocationKind.RESTORED
        ) == has_restore_source
        if (
            allocation.state_allocation_id != request.state_allocation_id
            or allocation.instance_id != request.instance_id
            or allocation.layout_id != template_revision.template.state_layout_id
            or allocation.status is not StateAllocationStatus.RESERVED
            or not kind_matches_restore_source
        ):
            raise RuntimeCompileError("runtime_state_invalid")
        if (
            request.owner_ref.owner_kind is RuntimeOwnerKind.PAPER_PROBE
            and allocation.kind is not StateAllocationKind.FRESH
        ):
            raise RuntimeCompileError("runtime_state_invalid")
        return allocation

    def _validate_secrets(
        self,
        request: CompileRuntimeRequest,
        template_revision: AdapterTemplateRevisionView,
    ) -> tuple[Identifier, ...]:
        try:
            references = tuple(
                _revalidate_model(SecretReference, reference)
                for reference in self._secret_references
            )
        except (TypeError, ValueError, ValidationError):
            raise RuntimeCompileError("runtime_secrets_invalid") from None

        reference_ids = [reference.secret_reference_id for reference in references]
        secret_classes = [reference.secret_class for reference in references]
        if (
            len(reference_ids) != len(set(reference_ids))
            or len(secret_classes) != len(set(secret_classes))
            or set(reference_ids) != set(request.secret_reference_ids)
            or set(secret_classes) != set(template_revision.template.secret_classes)
            or any(
                reference.status is not SecretReferenceStatus.ACTIVE
                or reference.owner_scope != request.owner_ref
                for reference in references
            )
        ):
            raise RuntimeCompileError("runtime_secrets_invalid")
        if (
            request.owner_ref.owner_kind is RuntimeOwnerKind.PAPER_PROBE
            and set(secret_classes) != _PAPER_PROBE_SECRET_CLASSES
        ):
            raise RuntimeCompileError("runtime_secrets_invalid")
        return tuple(sorted(reference_ids))

    @staticmethod
    def _validate_artifacts(request: CompileRuntimeRequest) -> None:
        config = request.config_identity
        strategy = request.strategy_identity
        safety = request.safety_policy_identity
        components = request.component_commits
        expected_dry_run = request.environment == "paper"
        if (
            config.market_scope != request.market_scope
            or config.commit != components.root_commit
            or safety.commit != components.root_commit
            or strategy.commit != components.strategies_commit
            or config.dry_run is not safety.dry_run
            or config.dry_run is not expected_dry_run
        ):
            raise RuntimeCompileError("runtime_artifacts_invalid")
        if request.owner_ref.owner_kind is RuntimeOwnerKind.PAPER_PROBE and (
            strategy.strategy_class_name != "SampleStrategy"
            or config.dry_run is not True
            or safety.dry_run is not True
        ):
            raise RuntimeCompileError("runtime_artifacts_invalid")

    def _validate_policies(
        self,
        template_revision: AdapterTemplateRevisionView,
    ) -> ClosedPolicySnapshot:
        try:
            policies = _revalidate_model(
                ClosedPolicySnapshot,
                self._closed_policy_snapshot,
            )
        except (TypeError, ValueError, ValidationError):
            raise RuntimeCompileError("runtime_policies_invalid") from None

        template = template_revision.template
        if (
            template.image_policy_id not in policies.image_policy_ids
            or template.command_policy_id not in policies.command_policy_ids
            or not set(template.mount_policy_ids) <= policies.mount_policy_ids
            or template.network_policy_id not in policies.network_policy_ids
            or template.health_profile_id not in policies.health_profile_ids
            or template.resource_profile_id not in policies.resource_profile_ids
            or template.state_layout_id not in policies.state_layout_ids
        ):
            raise RuntimeCompileError("runtime_policies_invalid")
        return policies

    @staticmethod
    def _validate_provenance(
        request: CompileRuntimeRequest,
        template_revision: AdapterTemplateRevisionView,
        policies: ClosedPolicySnapshot,
    ) -> None:
        components = request.component_commits
        if (
            components.root_commit != template_revision.root_commit
            or components.backend_commit != template_revision.backend_commit
            or components.frontend_commit != template_revision.frontend_commit
            or components.strategies_commit != template_revision.strategies_commit
            or policies.source_commit != components.root_commit
        ):
            raise RuntimeCompileError("runtime_provenance_invalid")

    @staticmethod
    def _build_payload(
        request: CompileRuntimeRequest,
        catalog_revision_id: Identifier,
        template_revision: AdapterTemplateRevisionView,
        state_allocation: StateAllocation,
        secret_reference_ids: tuple[Identifier, ...],
    ) -> RuntimeSpecPayload:
        template = template_revision.template
        components = request.component_commits
        return RuntimeSpecPayload(
            owner_ref=request.owner_ref,
            instance_kind=request.instance_kind,
            catalog_revision_id=catalog_revision_id,
            market_scope=request.market_scope,
            environment=request.environment,
            adapter_template_revision_id=template_revision.revision_id,
            template_digest=template_revision.payload_digest,
            image_policy_id=template.image_policy_id,
            command_policy_id=template.command_policy_id,
            mount_policy_ids=template.mount_policy_ids,
            network_policy_id=template.network_policy_id,
            health_profile_id=template.health_profile_id,
            resource_profile_id=template.resource_profile_id,
            state_layout_id=state_allocation.layout_id,
            state_allocation_id=state_allocation.state_allocation_id,
            secret_reference_ids=secret_reference_ids,
            config_blob_commit=request.config_identity.commit,
            strategy_commit=request.strategy_identity.commit,
            safety_policy_commit=request.safety_policy_identity.commit,
            root_commit=components.root_commit,
            backend_commit=components.backend_commit,
            frontend_commit=components.frontend_commit,
            strategies_commit=components.strategies_commit,
            config_blob_digest=request.config_identity.digest,
            strategy_digest=request.strategy_identity.digest,
            safety_policy_digest=request.safety_policy_identity.digest,
        )
