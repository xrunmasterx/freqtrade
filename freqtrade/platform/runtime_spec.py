import hashlib
import json

from pydantic import Field

from freqtrade.platform.runtime_domain import Identifier
from freqtrade.platform.template_domain import FrozenPlatformModel


class RuntimeSpecRevision(FrozenPlatformModel):
    runtime_spec_revision_id: Identifier
    canonical_payload: str
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "RuntimeSpecRevision":
        canonical_payload = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        payload_digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        return cls(
            runtime_spec_revision_id=f"runtime-spec-{payload_digest}",
            canonical_payload=canonical_payload,
            payload_digest=payload_digest,
        )
