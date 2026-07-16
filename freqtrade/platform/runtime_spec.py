import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from freqtrade.markets.catalog import ProductType
from freqtrade.markets.instrument import MarketType
from freqtrade.platform.runtime_domain import Identifier, RuntimeOwnerRef
from freqtrade.platform.template_domain import FrozenPlatformModel


_LowercaseSha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_GitObjectId = Annotated[str, Field(pattern=r"^([0-9a-f]{40}|[0-9a-f]{64})$")]
_InstrumentKey = Annotated[str, Field(min_length=1, max_length=256)]
_StrategyClassName = Annotated[
    str,
    Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"),
]


def _canonicalize(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


class RuntimeMarketScope(FrozenPlatformModel):
    market_id: MarketType
    product_ids: tuple[ProductType, ...] = Field(min_length=1)
    venue_ids: tuple[Identifier, ...] = ()
    instrument_keys: tuple[_InstrumentKey, ...] = ()

    @field_validator("product_ids", "venue_ids", "instrument_keys")
    @classmethod
    def require_unique_tuple(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate values are not allowed")
        return value


class RuntimeSpecPayload(FrozenPlatformModel):
    owner_ref: RuntimeOwnerRef
    instance_kind: Identifier
    catalog_revision_id: Identifier
    market_scope: RuntimeMarketScope
    environment: Literal["paper", "live"]
    adapter_template_revision_id: Identifier
    template_digest: _LowercaseSha256Digest
    image_policy_id: Identifier
    command_policy_id: Identifier
    mount_policy_ids: tuple[Identifier, ...] = Field(min_length=1)
    network_policy_id: Identifier
    health_profile_id: Identifier
    resource_profile_id: Identifier
    state_layout_id: Identifier
    state_allocation_id: Identifier
    secret_reference_ids: tuple[Identifier, ...]
    config_blob_commit: _GitObjectId
    strategy_commit: _GitObjectId
    strategy_class_name: _StrategyClassName | None = None
    safety_policy_commit: _GitObjectId
    root_commit: _GitObjectId
    backend_commit: _GitObjectId
    frontend_commit: _GitObjectId
    strategies_commit: _GitObjectId
    config_blob_digest: _LowercaseSha256Digest
    strategy_digest: _LowercaseSha256Digest
    safety_policy_digest: _LowercaseSha256Digest

    @field_validator("mount_policy_ids", "secret_reference_ids")
    @classmethod
    def require_unique_tuple(cls, value: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate values are not allowed")
        return value


class RuntimeSpecRevision(FrozenPlatformModel):
    runtime_spec_revision_id: Identifier
    canonical_payload: str
    payload_digest: str

    @model_validator(mode="after")
    def validate_envelope(self) -> "RuntimeSpecRevision":
        try:
            decoded_payload = json.loads(self.canonical_payload)
        except json.JSONDecodeError:
            raise ValueError("runtime_spec_payload_invalid_json") from None

        if not isinstance(decoded_payload, dict):
            raise ValueError("runtime_spec_payload_not_object")
        try:
            payload = RuntimeSpecPayload.model_validate(decoded_payload)
        except ValidationError:
            raise ValueError("runtime_spec_payload_invalid") from None

        canonical_payload = _canonicalize(payload.model_dump(mode="json", exclude_none=True))
        if canonical_payload != self.canonical_payload:
            raise ValueError("runtime_spec_payload_not_canonical")
        if len(self.payload_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.payload_digest
        ):
            raise ValueError("runtime_spec_payload_digest_invalid")

        expected_digest = hashlib.sha256(self.canonical_payload.encode("utf-8")).hexdigest()
        if self.payload_digest != expected_digest:
            raise ValueError("runtime_spec_payload_digest_mismatch")
        if self.runtime_spec_revision_id != f"runtime-spec-{expected_digest}":
            raise ValueError("runtime_spec_revision_id_mismatch")
        return self

    @classmethod
    def from_payload(
        cls,
        payload: RuntimeSpecPayload | dict[str, object],
    ) -> "RuntimeSpecRevision":
        validated_payload = RuntimeSpecPayload.model_validate(payload)
        canonical_payload = _canonicalize(
            validated_payload.model_dump(mode="json", exclude_none=True)
        )
        payload_digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        return cls(
            runtime_spec_revision_id=f"runtime-spec-{payload_digest}",
            canonical_payload=canonical_payload,
            payload_digest=payload_digest,
        )
