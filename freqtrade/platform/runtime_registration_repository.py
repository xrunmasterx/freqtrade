import json
from datetime import UTC, datetime

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import Engine, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from freqtrade.markets.default_catalog import CatalogSnapshot, default_catalog_snapshot
from freqtrade.platform.catalog_repository import CatalogRevisionRecord
from freqtrade.platform.runtime_compiler import (
    CommittedConfigIdentity,
    CommittedSafetyPolicyIdentity,
    CommittedStrategyIdentity,
    CompileRuntimeRequest,
    RuntimeCompileError,
    RuntimeSpecCompiler,
)
from freqtrade.platform.runtime_domain import (
    Identifier,
    RuntimeAuditAction,
    RuntimeDesiredState,
    RuntimeLifecycleStatus,
    RuntimeManagementMode,
    RuntimeOwnerKind,
    RuntimeOwnerRef,
)
from freqtrade.platform.runtime_models import (
    RuntimeAuditEventRecord,
    RuntimeInstanceRecord,
)
from freqtrade.platform.runtime_registration import (
    PAPER_PROBE_AUDIT_EVENT_ID,
    PAPER_PROBE_INSTANCE_ID,
    PAPER_PROBE_OWNER_REVISION,
    PAPER_PROBE_REQUEST_ID,
    PAPER_PROBE_SECRET_REFERENCE_IDS,
    PAPER_PROBE_STATE_ALLOCATION_ID,
    EnsurePaperProbeRegistrationRequest,
    PaperProbeRegistrationResult,
    PaperProbeRegistrationStatus,
)
from freqtrade.platform.runtime_spec import (
    RuntimeMarketScope,
    RuntimeSpecPayload,
    RuntimeSpecRevision,
)
from freqtrade.platform.template_domain import (
    SecretReference,
    SecretReferenceStatus,
    StateAllocation,
    StateAllocationKind,
    StateAllocationStatus,
    TemplateStatus,
)
from freqtrade.platform.template_models import (
    AdapterTemplateRevisionRecord,
    RuntimeSpecRevisionRecord,
    SecretReferenceRecord,
    StateAllocationRecord,
)
from freqtrade.platform.template_repository import (
    AdapterTemplateRevisionView,
    TemplateDataError,
    template_revision_view,
    template_transaction_lock_for,
)


_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_AUDIT_SOURCE = "runtime_registration_repository"
_CATALOG_REVISION_ID = "builtin-market-catalog-v2"
_PAPER_PROBE_INSTANCE_KIND = "freqtrade"
_PAPER_PROBE_RELATIVE_STATE_PATH = (
    f"ft_userdata/runtime/instances/{PAPER_PROBE_INSTANCE_ID}"
)
_PAPER_PROBE_OWNER = RuntimeOwnerRef(
    owner_kind=RuntimeOwnerKind.PAPER_PROBE,
    owner_id=PAPER_PROBE_INSTANCE_ID,
    owner_revision=PAPER_PROBE_OWNER_REVISION,
)
_PAPER_PROBE_MARKET_SCOPE = RuntimeMarketScope(
    market_id="digital_asset",
    product_ids=("spot",),
    venue_ids=("bitget",),
    instrument_keys=(),
)
_SECRET_IDENTITIES = (
    (PAPER_PROBE_SECRET_REFERENCE_IDS[0], "api_password", "api_password"),
    (PAPER_PROBE_SECRET_REFERENCE_IDS[1], "jwt_secret", "jwt_secret"),
    (PAPER_PROBE_SECRET_REFERENCE_IDS[2], "ws_token", "ws_token"),
)


class PaperProbeRegistrationConflict(RuntimeError):
    pass


class PaperProbeRegistrationNotFound(RuntimeError):
    pass


def _public_model_data(model: BaseModel) -> dict[str, object]:
    return model.model_dump(
        mode="python",
        include=set(type(model).model_fields),
        warnings=False,
    )


def _utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("registration_time_must_be_timezone_aware")
    return value.astimezone(UTC)


def _conflict() -> PaperProbeRegistrationConflict:
    return PaperProbeRegistrationConflict("paper_probe_registration_conflict")


def _raw_json_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _raw_json_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _raw_json_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


class SqlPaperProbeRegistrationRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._transaction_lock = template_transaction_lock_for(engine)

    def ensure_paper_probe_registration(
        self,
        request: EnsurePaperProbeRegistrationRequest,
        actor: Identifier,
        occurred_at: datetime,
    ) -> PaperProbeRegistrationResult:
        try:
            validated_request = EnsurePaperProbeRegistrationRequest.model_validate(
                _public_model_data(request)
            )
        except (TypeError, ValueError, ValidationError):
            raise _conflict() from None
        validated_actor = _IDENTIFIER_ADAPTER.validate_python(actor)
        normalized_time = _utc_time(occurred_at)

        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            self._transaction_lock.acquire(
                session,
                validated_request.adapter_template_revision_id,
            )
            template_revision = self._active_template(session, validated_request)
            self._transaction_lock.acquire(session, PAPER_PROBE_INSTANCE_ID)
            catalog = self._ensure_catalog(session, normalized_time)
            state_allocation, secret_references = self._ensure_inputs(
                session,
                template_revision.template.state_layout_id,
                normalized_time,
            )
            runtime_spec = self._compile(
                validated_request,
                catalog,
                template_revision,
                state_allocation,
                secret_references,
            )

            instance = session.get(RuntimeInstanceRecord, PAPER_PROBE_INSTANCE_ID)
            audit = session.get(RuntimeAuditEventRecord, PAPER_PROBE_AUDIT_EVENT_ID)
            if (instance is None) != (audit is None):
                raise _conflict()

            existing_spec = session.get(
                RuntimeSpecRevisionRecord,
                runtime_spec.runtime_spec_revision_id,
            )
            if instance is not None:
                if existing_spec is None:
                    raise _conflict()
                self._validate_spec_record(existing_spec, runtime_spec)
                return self._status_from_records(instance, existing_spec, audit)
            if existing_spec is not None:
                raise _conflict()

            spec_record = self._new_spec_record(
                runtime_spec,
                validated_request.adapter_template_revision_id,
                normalized_time,
            )
            session.add(spec_record)
            session.flush([spec_record])
            instance = self._new_instance(runtime_spec.runtime_spec_revision_id, normalized_time)
            session.add(instance)
            session.flush([instance])
            status = self._status(runtime_spec)
            session.add(
                self._new_audit(
                    validated_request,
                    status,
                    validated_actor,
                    normalized_time,
                )
            )
            return status

    def registration_status(self, instance_id: Identifier) -> PaperProbeRegistrationStatus:
        validated_instance_id = _IDENTIFIER_ADAPTER.validate_python(instance_id)
        with Session(self._engine) as session:
            instance = session.get(RuntimeInstanceRecord, validated_instance_id)
            if instance is None:
                raise PaperProbeRegistrationNotFound("paper_probe_registration_not_found")
            audit = session.get(RuntimeAuditEventRecord, PAPER_PROBE_AUDIT_EVENT_ID)
            spec = session.get(RuntimeSpecRevisionRecord, instance.runtime_spec_revision_id)
            if audit is None or spec is None:
                raise _conflict()
            return self._status_from_records(instance, spec, audit)

    @staticmethod
    def _active_template(
        session: Session,
        request: EnsurePaperProbeRegistrationRequest,
    ) -> AdapterTemplateRevisionView:
        record = session.get(
            AdapterTemplateRevisionRecord,
            request.adapter_template_revision_id,
        )
        if record is None:
            raise _conflict()
        try:
            revision = template_revision_view(record)
        except TemplateDataError:
            raise _conflict() from None
        if revision.status is not TemplateStatus.ACTIVE:
            raise _conflict()
        return revision

    @staticmethod
    def _catalog_insert(session: Session, values: dict[str, object]) -> None:
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            statement = postgresql_insert(CatalogRevisionRecord).values(**values)
        elif dialect_name == "sqlite":
            statement = sqlite_insert(CatalogRevisionRecord).values(**values)
        else:
            raise ValueError("unsupported_platform_database")
        session.execute(
            statement.on_conflict_do_nothing(
                index_elements=[CatalogRevisionRecord.revision_id]
            )
        )

    def _ensure_catalog(
        self,
        session: Session,
        occurred_at: datetime,
    ) -> CatalogSnapshot:
        expected = default_catalog_snapshot()
        expected_payload = expected.model_dump(mode="json")
        self._catalog_insert(
            session,
            {
                "revision_id": expected.revision_id,
                "payload": expected_payload,
                "created_at": occurred_at,
            },
        )
        record = session.get(CatalogRevisionRecord, expected.revision_id)
        if record is None or not _raw_json_equal(record.payload, expected_payload):
            raise _conflict()
        try:
            stored = CatalogSnapshot.model_validate(record.payload)
        except (TypeError, ValueError, ValidationError):
            raise _conflict() from None
        if stored != expected:
            raise _conflict()
        return stored

    def _ensure_inputs(
        self,
        session: Session,
        state_layout_id: str,
        occurred_at: datetime,
    ) -> tuple[StateAllocation, tuple[SecretReference, ...]]:
        allocation_rows = tuple(
            session.scalars(
                select(StateAllocationRecord).where(
                    or_(
                        StateAllocationRecord.state_allocation_id
                        == PAPER_PROBE_STATE_ALLOCATION_ID,
                        StateAllocationRecord.instance_id == PAPER_PROBE_INSTANCE_ID,
                        StateAllocationRecord.relative_path == _PAPER_PROBE_RELATIVE_STATE_PATH,
                    )
                )
            )
        )
        reference_rows = tuple(
            session.scalars(
                select(SecretReferenceRecord).where(
                    or_(
                        SecretReferenceRecord.secret_reference_id.in_(
                            PAPER_PROBE_SECRET_REFERENCE_IDS
                        ),
                        (
                            (SecretReferenceRecord.owner_kind == RuntimeOwnerKind.PAPER_PROBE)
                            & (SecretReferenceRecord.owner_id == PAPER_PROBE_INSTANCE_ID)
                            & (
                                SecretReferenceRecord.owner_revision
                                == PAPER_PROBE_OWNER_REVISION
                            )
                        ),
                    )
                )
            )
        )
        if not allocation_rows and not reference_rows:
            allocation_row = StateAllocationRecord(
                state_allocation_id=PAPER_PROBE_STATE_ALLOCATION_ID,
                instance_id=PAPER_PROBE_INSTANCE_ID,
                layout_id=state_layout_id,
                provider_id="managed-local-v1",
                relative_path=_PAPER_PROBE_RELATIVE_STATE_PATH,
                kind=StateAllocationKind.FRESH,
                status=StateAllocationStatus.RESERVED,
                generation=1,
                restore_source_bundle_id=None,
                created_at=occurred_at,
                ready_at=None,
                retired_at=None,
            )
            reference_rows = tuple(
                SecretReferenceRecord(
                    secret_reference_id=reference_id,
                    provider_id="local-file-v1",
                    secret_class=secret_class,
                    logical_name=logical_name,
                    owner_kind=RuntimeOwnerKind.PAPER_PROBE,
                    owner_id=PAPER_PROBE_INSTANCE_ID,
                    owner_revision=PAPER_PROBE_OWNER_REVISION,
                    status=SecretReferenceStatus.ACTIVE,
                    created_at=occurred_at,
                    retired_at=None,
                )
                for reference_id, secret_class, logical_name in _SECRET_IDENTITIES
            )
            session.add(allocation_row)
            session.add_all(reference_rows)
            session.flush([allocation_row, *reference_rows])
            allocation_rows = (allocation_row,)
        if len(allocation_rows) != 1 or len(reference_rows) != len(_SECRET_IDENTITIES):
            raise _conflict()
        allocation = self._allocation_view(allocation_rows[0], state_layout_id)
        references = self._reference_views(reference_rows)
        return allocation, references

    @staticmethod
    def _allocation_view(record: StateAllocationRecord, layout_id: str) -> StateAllocation:
        expected = {
            "state_allocation_id": PAPER_PROBE_STATE_ALLOCATION_ID,
            "instance_id": PAPER_PROBE_INSTANCE_ID,
            "layout_id": layout_id,
            "provider_id": "managed-local-v1",
            "relative_path": _PAPER_PROBE_RELATIVE_STATE_PATH,
            "kind": StateAllocationKind.FRESH.value,
            "status": StateAllocationStatus.RESERVED.value,
            "generation": 1,
            "restore_source_bundle_id": None,
            "ready_at": None,
            "retired_at": None,
        }
        if any(getattr(record, field_name) != value for field_name, value in expected.items()):
            raise _conflict()
        try:
            return StateAllocation(
                state_allocation_id=record.state_allocation_id,
                instance_id=record.instance_id,
                layout_id=record.layout_id,
                provider_id=record.provider_id,
                kind=record.kind,
                status=record.status,
                generation=record.generation,
                restore_source_bundle_id=record.restore_source_bundle_id,
            )
        except ValidationError:
            raise _conflict() from None

    @staticmethod
    def _reference_views(
        records: tuple[SecretReferenceRecord, ...],
    ) -> tuple[SecretReference, ...]:
        records_by_id = {record.secret_reference_id: record for record in records}
        if len(records_by_id) != len(_SECRET_IDENTITIES):
            raise _conflict()
        views = []
        for reference_id, secret_class, logical_name in _SECRET_IDENTITIES:
            record = records_by_id.get(reference_id)
            expected = {
                "provider_id": "local-file-v1",
                "secret_class": secret_class,
                "logical_name": logical_name,
                "owner_kind": RuntimeOwnerKind.PAPER_PROBE.value,
                "owner_id": PAPER_PROBE_INSTANCE_ID,
                "owner_revision": PAPER_PROBE_OWNER_REVISION,
                "status": SecretReferenceStatus.ACTIVE.value,
                "retired_at": None,
            }
            if record is None or any(
                getattr(record, field_name) != value for field_name, value in expected.items()
            ):
                raise _conflict()
            try:
                views.append(
                    SecretReference(
                        secret_reference_id=record.secret_reference_id,
                        provider_id=record.provider_id,
                        secret_class=record.secret_class,
                        logical_name=record.logical_name,
                        owner_scope=_PAPER_PROBE_OWNER,
                        status=record.status,
                    )
                )
            except ValidationError:
                raise _conflict() from None
        return tuple(views)

    @staticmethod
    def _compile(
        request: EnsurePaperProbeRegistrationRequest,
        catalog: CatalogSnapshot,
        template_revision: AdapterTemplateRevisionView,
        state_allocation: StateAllocation,
        secret_references: tuple[SecretReference, ...],
    ) -> RuntimeSpecRevision:
        compile_request = CompileRuntimeRequest(
            owner_ref=_PAPER_PROBE_OWNER,
            instance_id=PAPER_PROBE_INSTANCE_ID,
            instance_kind=_PAPER_PROBE_INSTANCE_KIND,
            catalog_revision_id=_CATALOG_REVISION_ID,
            market_scope=_PAPER_PROBE_MARKET_SCOPE,
            environment="paper",
            adapter_template_revision_id=request.adapter_template_revision_id,
            state_allocation_id=PAPER_PROBE_STATE_ALLOCATION_ID,
            secret_reference_ids=PAPER_PROBE_SECRET_REFERENCE_IDS,
            config_identity=CommittedConfigIdentity(
                commit=request.component_commits.root_commit,
                digest=request.config_blob_digest,
                market_scope=_PAPER_PROBE_MARKET_SCOPE,
                dry_run=True,
            ),
            strategy_identity=CommittedStrategyIdentity(
                commit=request.component_commits.root_commit,
                digest=request.strategy_digest,
                strategy_class_name=request.strategy_class_name,
            ),
            safety_policy_identity=CommittedSafetyPolicyIdentity(
                commit=request.component_commits.root_commit,
                digest=request.safety_policy_digest,
                dry_run=True,
            ),
            component_commits=request.component_commits,
        )
        compiler = RuntimeSpecCompiler(
            catalog_snapshot=catalog,
            template_revision=template_revision,
            state_allocation=state_allocation,
            secret_references=secret_references,
            closed_policy_snapshot=request.closed_policy_snapshot,
        )
        try:
            return compiler.compile(compile_request)
        except RuntimeCompileError:
            raise _conflict() from None

    @staticmethod
    def _new_spec_record(
        runtime_spec: RuntimeSpecRevision,
        template_revision_id: str,
        occurred_at: datetime,
    ) -> RuntimeSpecRevisionRecord:
        return RuntimeSpecRevisionRecord(
            runtime_spec_revision_id=runtime_spec.runtime_spec_revision_id,
            owner_kind=RuntimeOwnerKind.PAPER_PROBE,
            owner_id=PAPER_PROBE_INSTANCE_ID,
            owner_revision=PAPER_PROBE_OWNER_REVISION,
            instance_kind=_PAPER_PROBE_INSTANCE_KIND,
            catalog_revision_id=_CATALOG_REVISION_ID,
            environment="paper",
            adapter_template_revision_id=template_revision_id,
            state_allocation_id=PAPER_PROBE_STATE_ALLOCATION_ID,
            canonical_payload=runtime_spec.canonical_payload,
            payload_digest=runtime_spec.payload_digest,
            created_at=occurred_at,
        )

    @staticmethod
    def _validate_spec_record(
        record: RuntimeSpecRevisionRecord,
        runtime_spec: RuntimeSpecRevision,
    ) -> None:
        expected = SqlPaperProbeRegistrationRepository._new_spec_record(
            runtime_spec,
            json.loads(runtime_spec.canonical_payload)["adapter_template_revision_id"],
            record.created_at,
        )
        fields = tuple(
            column.name
            for column in RuntimeSpecRevisionRecord.__table__.columns
            if column.name != "created_at"
        )
        if any(getattr(record, field) != getattr(expected, field) for field in fields):
            raise _conflict()

    @staticmethod
    def _new_instance(
        runtime_spec_revision_id: str,
        occurred_at: datetime,
    ) -> RuntimeInstanceRecord:
        return RuntimeInstanceRecord(
            instance_id=PAPER_PROBE_INSTANCE_ID,
            instance_kind=_PAPER_PROBE_INSTANCE_KIND,
            owner_kind=RuntimeOwnerKind.PAPER_PROBE,
            owner_id=PAPER_PROBE_INSTANCE_ID,
            owner_revision=PAPER_PROBE_OWNER_REVISION,
            management_mode=RuntimeManagementMode.SUPERVISOR,
            runtime_spec_revision_id=runtime_spec_revision_id,
            environment="paper",
            state_allocation_id=PAPER_PROBE_STATE_ALLOCATION_ID,
            desired_state=RuntimeDesiredState.STOPPED,
            lifecycle_status=RuntimeLifecycleStatus.REGISTERED,
            failure_latched=False,
            optimistic_version=0,
            created_at=occurred_at,
            retired_at=None,
        )

    @staticmethod
    def _status(runtime_spec: RuntimeSpecRevision) -> PaperProbeRegistrationStatus:
        payload = RuntimeSpecPayload.model_validate(json.loads(runtime_spec.canonical_payload))
        return PaperProbeRegistrationStatus(
            instance_id=PAPER_PROBE_INSTANCE_ID,
            runtime_spec_revision_id=runtime_spec.runtime_spec_revision_id,
            adapter_template_revision_id=payload.adapter_template_revision_id,
            catalog_revision_id=payload.catalog_revision_id,
            state_allocation_id=payload.state_allocation_id,
            secret_reference_ids=payload.secret_reference_ids,
            desired_state="stopped",
            lifecycle_status="registered",
        )

    @staticmethod
    def _new_audit(
        request: EnsurePaperProbeRegistrationRequest,
        status: PaperProbeRegistrationStatus,
        actor: str,
        occurred_at: datetime,
    ) -> RuntimeAuditEventRecord:
        commits = request.component_commits
        return RuntimeAuditEventRecord(
            audit_event_id=PAPER_PROBE_AUDIT_EVENT_ID,
            actor_type=actor,
            request_id=PAPER_PROBE_REQUEST_ID,
            idempotency_key=None,
            owner_kind=RuntimeOwnerKind.PAPER_PROBE,
            owner_id=PAPER_PROBE_INSTANCE_ID,
            owner_revision=PAPER_PROBE_OWNER_REVISION,
            instance_id=PAPER_PROBE_INSTANCE_ID,
            runtime_spec_revision_id=status.runtime_spec_revision_id,
            adapter_template_revision_id=status.adapter_template_revision_id,
            action=RuntimeAuditAction.REGISTER_PAPER_PROBE,
            previous_state=None,
            next_state=status.model_dump(mode="json"),
            result_code="registered",
            occurred_at=occurred_at,
            provenance={
                "source": _AUDIT_SOURCE,
                "catalog_revision_id": status.catalog_revision_id,
                "runtime_spec_digest": status.runtime_spec_revision_id.removeprefix(
                    "runtime-spec-"
                ),
                "template_digest": status.adapter_template_revision_id.removeprefix(
                    "template-"
                ),
                "root_commit": commits.root_commit,
                "backend_commit": commits.backend_commit,
                "frontend_commit": commits.frontend_commit,
                "strategies_commit": commits.strategies_commit,
                "config_blob_digest": request.config_blob_digest,
                "strategy_digest": request.strategy_digest,
                "safety_policy_digest": request.safety_policy_digest,
            },
        )

    @staticmethod
    def _status_from_records(
        instance: RuntimeInstanceRecord,
        spec: RuntimeSpecRevisionRecord,
        audit: RuntimeAuditEventRecord,
    ) -> PaperProbeRegistrationStatus:
        instance_expected = {
            "instance_id": PAPER_PROBE_INSTANCE_ID,
            "instance_kind": _PAPER_PROBE_INSTANCE_KIND,
            "owner_kind": RuntimeOwnerKind.PAPER_PROBE.value,
            "owner_id": PAPER_PROBE_INSTANCE_ID,
            "owner_revision": PAPER_PROBE_OWNER_REVISION,
            "management_mode": RuntimeManagementMode.SUPERVISOR.value,
            "runtime_spec_revision_id": spec.runtime_spec_revision_id,
            "environment": "paper",
            "state_allocation_id": PAPER_PROBE_STATE_ALLOCATION_ID,
            "desired_state": RuntimeDesiredState.STOPPED.value,
            "lifecycle_status": RuntimeLifecycleStatus.REGISTERED.value,
            "failure_latched": False,
            "optimistic_version": 0,
            "retired_at": None,
        }
        if any(
            getattr(instance, field_name) != value
            for field_name, value in instance_expected.items()
        ):
            raise _conflict()
        try:
            revision = RuntimeSpecRevision(
                runtime_spec_revision_id=spec.runtime_spec_revision_id,
                canonical_payload=spec.canonical_payload,
                payload_digest=spec.payload_digest,
            )
            payload = RuntimeSpecPayload.model_validate(json.loads(spec.canonical_payload))
        except (TypeError, ValueError, ValidationError):
            raise _conflict() from None
        spec_expected = {
            "owner_kind": RuntimeOwnerKind.PAPER_PROBE.value,
            "owner_id": PAPER_PROBE_INSTANCE_ID,
            "owner_revision": PAPER_PROBE_OWNER_REVISION,
            "instance_kind": _PAPER_PROBE_INSTANCE_KIND,
            "catalog_revision_id": payload.catalog_revision_id,
            "environment": "paper",
            "adapter_template_revision_id": payload.adapter_template_revision_id,
            "state_allocation_id": PAPER_PROBE_STATE_ALLOCATION_ID,
        }
        if any(getattr(spec, field_name) != value for field_name, value in spec_expected.items()):
            raise _conflict()
        status = SqlPaperProbeRegistrationRepository._status(revision)
        audit_expected = {
            "audit_event_id": PAPER_PROBE_AUDIT_EVENT_ID,
            "request_id": PAPER_PROBE_REQUEST_ID,
            "idempotency_key": None,
            "owner_kind": RuntimeOwnerKind.PAPER_PROBE.value,
            "owner_id": PAPER_PROBE_INSTANCE_ID,
            "owner_revision": PAPER_PROBE_OWNER_REVISION,
            "instance_id": PAPER_PROBE_INSTANCE_ID,
            "runtime_spec_revision_id": status.runtime_spec_revision_id,
            "adapter_template_revision_id": status.adapter_template_revision_id,
            "action": RuntimeAuditAction.REGISTER_PAPER_PROBE.value,
            "previous_state": None,
            "next_state": status.model_dump(mode="json"),
            "result_code": "registered",
            "provenance": {
                "source": _AUDIT_SOURCE,
                "catalog_revision_id": payload.catalog_revision_id,
                "runtime_spec_digest": spec.payload_digest,
                "template_digest": payload.template_digest,
                "root_commit": payload.root_commit,
                "backend_commit": payload.backend_commit,
                "frontend_commit": payload.frontend_commit,
                "strategies_commit": payload.strategies_commit,
                "config_blob_digest": payload.config_blob_digest,
                "strategy_digest": payload.strategy_digest,
                "safety_policy_digest": payload.safety_policy_digest,
            },
        }
        try:
            _IDENTIFIER_ADAPTER.validate_python(audit.actor_type)
        except ValidationError:
            raise _conflict() from None
        if not isinstance(audit.occurred_at, datetime):
            raise _conflict()
        if any(getattr(audit, field_name) != value for field_name, value in audit_expected.items()):
            raise _conflict()
        return status
