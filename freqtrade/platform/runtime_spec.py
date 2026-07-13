import hashlib
import json

from pydantic import model_validator

from freqtrade.platform.runtime_domain import Identifier
from freqtrade.platform.template_domain import FrozenPlatformModel


_SENSITIVE_PAYLOAD_KEYS = {
    "api_key",
    "api_secret",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "dsn",
    "host_path",
    "host_paths",
    "password",
    "passwords",
    "private_key",
    "secret_content",
    "secret_content_hash",
    "secret_hash",
    "secret_path",
    "secret_paths",
    "secret_value",
    "secret_values",
    "token",
    "tokens",
}


def _canonicalize(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in _SENSITIVE_PAYLOAD_KEYS:
                raise ValueError("runtime_spec_sensitive_key_forbidden")
            _reject_sensitive_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_keys(child)


class RuntimeSpecRevision(FrozenPlatformModel):
    runtime_spec_revision_id: Identifier
    canonical_payload: str
    payload_digest: str

    @model_validator(mode="after")
    def validate_envelope(self) -> "RuntimeSpecRevision":
        try:
            payload = json.loads(self.canonical_payload)
        except json.JSONDecodeError:
            raise ValueError("runtime_spec_payload_invalid_json") from None

        if _canonicalize(payload) != self.canonical_payload:
            raise ValueError("runtime_spec_payload_not_canonical")
        _reject_sensitive_keys(payload)
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
    def from_payload(cls, payload: dict[str, object]) -> "RuntimeSpecRevision":
        _reject_sensitive_keys(payload)
        canonical_payload = _canonicalize(payload)
        payload_digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        return cls(
            runtime_spec_revision_id=f"runtime-spec-{payload_digest}",
            canonical_payload=canonical_payload,
            payload_digest=payload_digest,
        )
