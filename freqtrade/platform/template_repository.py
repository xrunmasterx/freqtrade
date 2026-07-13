import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from freqtrade.platform.runtime_domain import Identifier, RuntimeAuditAction
from freqtrade.platform.runtime_models import RuntimeAuditEventRecord
from freqtrade.platform.template_domain import AdapterTemplate, FrozenPlatformModel, TemplateStatus
from freqtrade.platform.template_models import AdapterTemplateRevisionRecord


IdFactory = Callable[[str], str]
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_LowercaseSha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_GitObjectId = Annotated[str, Field(pattern=r"^([0-9a-f]{40}|[0-9a-f]{64})$")]
_TEMPLATE_PAYLOAD_KEYS = frozenset({"schema_version", *AdapterTemplate.model_fields})
_AUDIT_SOURCE = "template_repository"


class TemplateConflict(RuntimeError):
    pass


class TemplateNotFound(RuntimeError):
    pass


class TemplateDataError(RuntimeError):
    pass


class TemplateInvalidTransition(RuntimeError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_non_finite_constant(_value: str) -> None:
    raise ValueError("non_finite_json_number")


def _canonicalize(template: AdapterTemplate) -> str:
    payload = {"schema_version": 1, **template.model_dump(mode="json")}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"


def _decode_template_payload(canonical_payload: str) -> AdapterTemplate:
    if not isinstance(canonical_payload, str):
        raise ValueError("template_payload_invalid")
    try:
        decoded = json.loads(
            canonical_payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, ValueError):
        raise ValueError("template_payload_invalid") from None
    if (
        not isinstance(decoded, dict)
        or set(decoded) != _TEMPLATE_PAYLOAD_KEYS
        or type(decoded.get("schema_version")) is not int
        or decoded["schema_version"] != 1
    ):
        raise ValueError("template_payload_invalid")
    try:
        return AdapterTemplate.model_validate(
            {key: value for key, value in decoded.items() if key != "schema_version"}
        )
    except ValidationError:
        raise ValueError("template_payload_invalid") from None


class CommittedTemplatePublication(FrozenPlatformModel):
    template: AdapterTemplate
    canonical_payload: str
    payload_digest: str
    source_commit: str
    root_commit: str
    backend_commit: str
    frontend_commit: str
    strategies_commit: str

    @field_validator("payload_digest")
    @classmethod
    def validate_payload_digest(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("template_payload_digest_invalid")
        return value

    @field_validator(
        "source_commit",
        "root_commit",
        "backend_commit",
        "frontend_commit",
        "strategies_commit",
    )
    @classmethod
    def validate_commit(cls, value: str) -> str:
        if len(value) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("template_commit_invalid")
        return value

    @model_validator(mode="after")
    def validate_committed_payload(self) -> "CommittedTemplatePublication":
        if self.source_commit != self.root_commit:
            raise ValueError("template_source_commit_mismatch")
        decoded_template = _decode_template_payload(self.canonical_payload)
        if decoded_template != self.template:
            raise ValueError("template_payload_template_mismatch")
        if self.canonical_payload != _canonicalize(decoded_template):
            raise ValueError("template_payload_invalid")
        try:
            encoded_payload = self.canonical_payload.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("template_payload_invalid") from None
        expected_digest = hashlib.sha256(encoded_payload).hexdigest()
        if self.payload_digest != expected_digest:
            raise ValueError("template_payload_digest_mismatch")
        return self


class AdapterTemplateRevisionView(FrozenPlatformModel):
    revision_id: Identifier
    template: AdapterTemplate
    payload_digest: _LowercaseSha256Digest
    source_commit: _GitObjectId
    root_commit: _GitObjectId
    backend_commit: _GitObjectId
    frontend_commit: _GitObjectId
    strategies_commit: _GitObjectId
    status: TemplateStatus
    published_by: Identifier
    published_at: AwareDatetime
    deprecated_at: AwareDatetime | None
    revoked_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_identity(self) -> "AdapterTemplateRevisionView":
        if self.revision_id != f"template-{self.payload_digest}":
            raise ValueError("template_revision_id_mismatch")
        if self.source_commit != self.root_commit:
            raise ValueError("template_source_commit_mismatch")
        if self.status is TemplateStatus.ACTIVE and (
            self.deprecated_at is not None or self.revoked_at is not None
        ):
            raise ValueError("template_status_timestamp_mismatch")
        if self.status is TemplateStatus.DEPRECATED and (
            self.deprecated_at is None or self.revoked_at is not None
        ):
            raise ValueError("template_status_timestamp_mismatch")
        if self.status is TemplateStatus.REVOKED and self.revoked_at is None:
            raise ValueError("template_status_timestamp_mismatch")
        return self


def _revalidate_publication(
    publication: CommittedTemplatePublication,
) -> CommittedTemplatePublication:
    template = publication.template
    template_fields = (
        dict(template.__dict__)
        if isinstance(template, AdapterTemplate)
        else template
    )
    values = dict(publication.__dict__)
    values["template"] = template_fields
    return CommittedTemplatePublication.model_validate(values)


def _uuid_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("template_time_must_be_timezone_aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError("template_timestamp_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _stored_required_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("template_timestamp_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlTemplateRepository:
    def __init__(self, engine: Engine, id_factory: IdFactory = _uuid_id) -> None:
        self._engine = engine
        self._id_factory = id_factory

    def publish_template(
        self,
        committed_template: CommittedTemplatePublication,
        actor: Identifier,
        published_at: datetime,
    ) -> AdapterTemplateRevisionView:
        validated_actor = _IDENTIFIER_ADAPTER.validate_python(actor)
        normalized_time = _utc_time(published_at)
        validated_publication = _revalidate_publication(committed_template)
        template = validated_publication.template
        with Session(self._engine, expire_on_commit=False) as session:
            existing = self._find_version(
                session,
                template.template_id,
                template.semantic_version,
            )
            if existing is not None:
                return self._resolve_existing(existing, validated_publication.payload_digest)
            record = AdapterTemplateRevisionRecord(
                adapter_template_revision_id=f"template-{validated_publication.payload_digest}",
                template_id=template.template_id,
                semantic_version=template.semantic_version,
                canonical_payload=validated_publication.canonical_payload,
                payload_digest=validated_publication.payload_digest,
                source_commit=validated_publication.source_commit,
                root_commit=validated_publication.root_commit,
                backend_commit=validated_publication.backend_commit,
                frontend_commit=validated_publication.frontend_commit,
                strategies_commit=validated_publication.strategies_commit,
                status=TemplateStatus.ACTIVE,
                published_by=validated_actor,
                published_at=normalized_time,
                deprecated_at=None,
                revoked_at=None,
            )
            session.add(record)
            try:
                session.flush([record])
            except IntegrityError as error:
                session.rollback()
                existing = self._find_version(
                    session,
                    template.template_id,
                    template.semantic_version,
                )
                if existing is None:
                    raise error
                return self._resolve_existing(existing, validated_publication.payload_digest)
            try:
                self._append_audit(
                    session,
                    record,
                    validated_actor,
                    RuntimeAuditAction.PUBLISH_TEMPLATE,
                    None,
                    {"status": TemplateStatus.ACTIVE.value},
                    "published",
                    normalized_time,
                )
                view = self._view(record)
                session.commit()
            except Exception:
                session.rollback()
                raise
            return view

    def get_template_revision(self, revision_id: Identifier) -> AdapterTemplateRevisionView:
        validated_revision_id = _IDENTIFIER_ADAPTER.validate_python(revision_id)
        with Session(self._engine) as session:
            record = session.get(AdapterTemplateRevisionRecord, validated_revision_id)
            if record is None:
                raise TemplateNotFound("template_revision_not_found")
            return self._view(record)

    def deprecate_template(
        self,
        revision_id: Identifier,
        actor: Identifier,
        deprecated_at: datetime,
    ) -> AdapterTemplateRevisionView:
        return self._transition(
            revision_id,
            actor,
            TemplateStatus.DEPRECATED,
            _utc_time(deprecated_at),
        )

    def revoke_template(
        self,
        revision_id: Identifier,
        actor: Identifier,
        revoked_at: datetime,
    ) -> AdapterTemplateRevisionView:
        return self._transition(
            revision_id,
            actor,
            TemplateStatus.REVOKED,
            _utc_time(revoked_at),
        )

    def _transition(
        self,
        revision_id: Identifier,
        actor: Identifier,
        target_status: TemplateStatus,
        occurred_at: datetime,
    ) -> AdapterTemplateRevisionView:
        validated_revision_id = _IDENTIFIER_ADAPTER.validate_python(revision_id)
        validated_actor = _IDENTIFIER_ADAPTER.validate_python(actor)
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            record = session.scalar(
                select(AdapterTemplateRevisionRecord)
                .where(
                    AdapterTemplateRevisionRecord.adapter_template_revision_id
                    == validated_revision_id
                )
                .with_for_update()
            )
            if record is None:
                raise TemplateNotFound("template_revision_not_found")
            current_view = self._view(record)
            current_status = current_view.status
            if current_status is target_status:
                return current_view
            if current_status is TemplateStatus.REVOKED or (
                target_status is TemplateStatus.DEPRECATED
                and current_status is not TemplateStatus.ACTIVE
            ):
                raise TemplateInvalidTransition("template_status_transition_invalid")

            previous_state = self._audit_state(record)
            record.status = target_status
            if target_status is TemplateStatus.DEPRECATED:
                record.deprecated_at = occurred_at
                action = RuntimeAuditAction.DEPRECATE_TEMPLATE
                result_code = "deprecated"
            else:
                record.revoked_at = occurred_at
                action = RuntimeAuditAction.REVOKE_TEMPLATE
                result_code = "revoked"
            self._append_audit(
                session,
                record,
                validated_actor,
                action,
                previous_state,
                self._audit_state(record),
                result_code,
                occurred_at,
            )
            return self._view(record)

    def _append_audit(
        self,
        session: Session,
        record: AdapterTemplateRevisionRecord,
        actor: str,
        action: RuntimeAuditAction,
        previous_state: dict[str, str] | None,
        next_state: dict[str, str],
        result_code: str,
        occurred_at: datetime,
    ) -> None:
        session.add(
            RuntimeAuditEventRecord(
                audit_event_id=self._new_id("audit"),
                actor_type=actor,
                request_id=self._new_id("request"),
                idempotency_key=None,
                owner_kind=None,
                owner_id=None,
                owner_revision=None,
                instance_id=None,
                runtime_spec_revision_id=None,
                adapter_template_revision_id=record.adapter_template_revision_id,
                action=action.value,
                previous_state=previous_state,
                next_state=next_state,
                result_code=result_code,
                occurred_at=occurred_at,
                provenance={
                    "source": _AUDIT_SOURCE,
                    "payload_digest": record.payload_digest,
                    "source_commit": record.source_commit,
                    "root_commit": record.root_commit,
                    "backend_commit": record.backend_commit,
                    "frontend_commit": record.frontend_commit,
                    "strategies_commit": record.strategies_commit,
                },
            )
        )

    def _new_id(self, prefix: str) -> str:
        return _IDENTIFIER_ADAPTER.validate_python(self._id_factory(prefix))

    @staticmethod
    def _find_version(
        session: Session,
        template_id: str,
        semantic_version: str,
    ) -> AdapterTemplateRevisionRecord | None:
        return session.scalar(
            select(AdapterTemplateRevisionRecord).where(
                AdapterTemplateRevisionRecord.template_id == template_id,
                AdapterTemplateRevisionRecord.semantic_version == semantic_version,
            )
        )

    @classmethod
    def _resolve_existing(
        cls,
        record: AdapterTemplateRevisionRecord,
        payload_digest: str,
    ) -> AdapterTemplateRevisionView:
        if record.payload_digest != payload_digest:
            raise TemplateConflict("template_version_digest_conflict")
        return cls._view(record)

    @staticmethod
    def _audit_state(record: AdapterTemplateRevisionRecord) -> dict[str, str]:
        state = {"status": TemplateStatus(record.status).value}
        deprecated_at = _stored_utc(record.deprecated_at)
        revoked_at = _stored_utc(record.revoked_at)
        if deprecated_at is not None:
            state["deprecated_at"] = deprecated_at.isoformat()
        if revoked_at is not None:
            state["revoked_at"] = revoked_at.isoformat()
        return state

    @staticmethod
    def _view(record: AdapterTemplateRevisionRecord) -> AdapterTemplateRevisionView:
        try:
            template = _decode_template_payload(record.canonical_payload)
            publication = CommittedTemplatePublication.model_validate(
                {
                    "template": template.model_dump(mode="python"),
                    "canonical_payload": record.canonical_payload,
                    "payload_digest": record.payload_digest,
                    "source_commit": record.source_commit,
                    "root_commit": record.root_commit,
                    "backend_commit": record.backend_commit,
                    "frontend_commit": record.frontend_commit,
                    "strategies_commit": record.strategies_commit,
                }
            )
            if (
                publication.template.template_id != record.template_id
                or publication.template.semantic_version != record.semantic_version
            ):
                raise ValueError("template_record_identity_mismatch")
            return AdapterTemplateRevisionView(
                revision_id=record.adapter_template_revision_id,
                template=publication.template,
                payload_digest=publication.payload_digest,
                source_commit=publication.source_commit,
                root_commit=publication.root_commit,
                backend_commit=publication.backend_commit,
                frontend_commit=publication.frontend_commit,
                strategies_commit=publication.strategies_commit,
                status=TemplateStatus(record.status),
                published_by=record.published_by,
                published_at=_stored_required_utc(record.published_at),
                deprecated_at=_stored_utc(record.deprecated_at),
                revoked_at=_stored_utc(record.revoked_at),
            )
        except (ValidationError, ValueError):
            raise TemplateDataError("invalid_template_revision_data") from None
