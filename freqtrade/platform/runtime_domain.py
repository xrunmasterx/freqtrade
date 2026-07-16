from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")]


class _RuntimeDomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RuntimeOwnerKind(StrEnum):
    MIGRATION_BOT = "migration_bot"
    PAPER_PROBE = "paper_probe"
    WORKSPACE_WORKER = "workspace_worker"


class RuntimeManagementMode(StrEnum):
    SUPERVISOR = "supervisor"


class RuntimeDesiredState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    RETIRED = "retired"


class RuntimeLifecycleStatus(StrEnum):
    REGISTERED = "registered"
    PROVISIONING = "provisioning"
    STOPPED = "stopped"
    STARTING = "starting"
    HEALTHY = "healthy"
    STOPPING = "stopping"
    FAILED = "failed"
    RETIRED = "retired"


class RuntimeAttemptStatus(StrEnum):
    PENDING = "pending"
    VALIDATING = "validating"
    LAUNCHING = "launching"
    HEALTHY = "healthy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class RuntimeAction(StrEnum):
    START = "start"
    STOP = "stop"
    RETRY = "retry"
    RETIRE = "retire"


class RuntimeAuditAction(StrEnum):
    START = "start"
    STOP = "stop"
    RETRY = "retry"
    RETIRE = "retire"
    PUBLISH_TEMPLATE = "publish_template"
    DEPRECATE_TEMPLATE = "deprecate_template"
    REVOKE_TEMPLATE = "revoke_template"
    REGISTER_PAPER_PROBE = "register_paper_probe"


class RuntimeJobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"


class RuntimeOwnerRef(_RuntimeDomainModel):
    owner_kind: RuntimeOwnerKind
    owner_id: Identifier
    owner_revision: Identifier


class RuntimeInstanceView(_RuntimeDomainModel):
    instance_id: Identifier
    instance_kind: Identifier
    owner_ref: RuntimeOwnerRef
    management_mode: RuntimeManagementMode
    runtime_spec_revision_id: Identifier
    environment: Literal["paper", "live"]
    state_allocation_id: Identifier
    desired_state: RuntimeDesiredState
    lifecycle_status: RuntimeLifecycleStatus
    failure_latched: bool
    optimistic_version: int = Field(ge=0)
    created_at: AwareDatetime
    retired_at: AwareDatetime | None


class RuntimeAttemptView(_RuntimeDomainModel):
    attempt_id: Identifier
    instance_id: Identifier
    attempt_number: int = Field(ge=1)
    runtime_spec_revision_id: Identifier
    adapter_template_revision_id: Identifier
    status: RuntimeAttemptStatus
    health_result: Identifier | None
    started_at: AwareDatetime | None
    stopped_at: AwareDatetime | None
    exit_code: int | None
    failure_code: Identifier | None


class RuntimeJobView(_RuntimeDomainModel):
    job_id: Identifier
    instance_id: Identifier
    requested_action: RuntimeAction
    idempotency_key: Identifier
    expected_instance_version: int = Field(ge=0)
    status: RuntimeJobStatus
    lease_owner: Identifier | None
    lease_generation: int = Field(ge=0)
    lease_expires_at: AwareDatetime | None
    requested_at: AwareDatetime
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    failure_code: Identifier | None


class RuntimeLifecycleCommand(_RuntimeDomainModel):
    instance_id: Identifier
    action: RuntimeAction
    idempotency_key: Identifier
    expected_instance_version: int = Field(ge=0)
