from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field

from freqtrade.platform.runtime_compiler import ClosedPolicySnapshot, ComponentCommits
from freqtrade.platform.runtime_domain import Identifier
from freqtrade.platform.template_domain import FrozenPlatformModel


_LowercaseSha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

PAPER_PROBE_INSTANCE_ID = "phase2-spot-paper-probe"
PAPER_PROBE_OWNER_REVISION = "phase2-spot-paper-probe-v1"
PAPER_PROBE_STATE_ALLOCATION_ID = "state-phase2-spot-paper-probe-v1"
PAPER_PROBE_SECRET_REFERENCE_IDS = (
    "secret-phase2-spot-paper-probe-api-password-v1",
    "secret-phase2-spot-paper-probe-jwt-secret-v1",
    "secret-phase2-spot-paper-probe-ws-token-v1",
)
PAPER_PROBE_AUDIT_EVENT_ID = "audit-register-phase2-spot-paper-probe"
PAPER_PROBE_REQUEST_ID = "request-register-phase2-spot-paper-probe"


class EnsurePaperProbeRegistrationRequest(FrozenPlatformModel):
    adapter_template_revision_id: Identifier
    component_commits: ComponentCommits
    config_blob_digest: _LowercaseSha256Digest
    strategy_digest: _LowercaseSha256Digest
    safety_policy_digest: _LowercaseSha256Digest
    strategy_class_name: Literal["SampleStrategy"]
    closed_policy_snapshot: ClosedPolicySnapshot


class PaperProbeRegistrationStatus(FrozenPlatformModel):
    instance_id: Identifier
    runtime_spec_revision_id: Identifier
    adapter_template_revision_id: Identifier
    catalog_revision_id: Identifier
    state_allocation_id: Identifier
    secret_reference_ids: tuple[Identifier, ...]
    desired_state: Literal["stopped"]
    lifecycle_status: Literal["registered"]


PaperProbeRegistrationResult = PaperProbeRegistrationStatus


class PaperProbeRegistrationRepository(Protocol):
    def ensure_paper_probe_registration(
        self,
        request: EnsurePaperProbeRegistrationRequest,
        actor: Identifier,
        occurred_at: datetime,
    ) -> PaperProbeRegistrationResult: ...

    def registration_status(self, instance_id: Identifier) -> PaperProbeRegistrationStatus: ...
