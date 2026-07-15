from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from freqtrade.platform.runtime_domain import Identifier, RuntimeOwnerKind, RuntimeOwnerRef


class FrozenPlatformModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class TemplateStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    REVOKED = "revoked"


class StateAllocationStatus(StrEnum):
    RESERVED = "reserved"
    PROVISIONING = "provisioning"
    READY = "ready"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class StateAllocationKind(StrEnum):
    FRESH = "fresh"
    RESTORED = "restored"


class SecretReferenceStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    RETIRED = "retired"


class AdapterTemplate(FrozenPlatformModel):
    template_id: Identifier
    semantic_version: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
    )
    allowed_instance_kinds: tuple[Identifier, ...] = Field(min_length=1)
    allowed_owner_kinds: tuple[RuntimeOwnerKind, ...] = Field(min_length=1)
    allowed_environments: tuple[Literal["paper", "live"], ...] = Field(min_length=1)
    image_policy_id: Identifier
    command_policy_id: Identifier
    mount_policy_ids: tuple[Identifier, ...] = Field(min_length=1)
    network_policy_id: Identifier
    health_profile_id: Identifier
    resource_profile_id: Identifier
    secret_classes: tuple[Identifier, ...] = Field(min_length=1)
    state_layout_id: Identifier

    @field_validator(
        "allowed_instance_kinds",
        "allowed_owner_kinds",
        "allowed_environments",
        "mount_policy_ids",
        "secret_classes",
    )
    @classmethod
    def require_unique_tuple(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate values are not allowed")
        return value


class StateAllocation(FrozenPlatformModel):
    state_allocation_id: Identifier
    instance_id: Identifier
    layout_id: Identifier
    provider_id: Literal["managed-local-v1"]
    kind: StateAllocationKind
    status: StateAllocationStatus
    generation: int = Field(ge=1)
    restore_source_bundle_id: Identifier | None = None

    @computed_field
    @property
    def relative_path(self) -> str:
        return f"ft_userdata/runtime/instances/{self.instance_id}"


class SecretReference(FrozenPlatformModel):
    secret_reference_id: Identifier
    provider_id: Literal["local-file-v1"]
    secret_class: Identifier
    logical_name: Identifier
    owner_scope: RuntimeOwnerRef
    status: SecretReferenceStatus
