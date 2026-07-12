from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import freqtrade.platform as platform
from freqtrade.platform.runtime_domain import (
    RuntimeAction,
    RuntimeAttemptStatus,
    RuntimeAttemptView,
    RuntimeDesiredState,
    RuntimeInstanceView,
    RuntimeJobStatus,
    RuntimeJobView,
    RuntimeLifecycleCommand,
    RuntimeLifecycleStatus,
    RuntimeManagementMode,
    RuntimeOwnerKind,
    RuntimeOwnerRef,
)


NOW = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)


def _owner() -> RuntimeOwnerRef:
    return RuntimeOwnerRef(
        owner_kind=RuntimeOwnerKind.MIGRATION_BOT,
        owner_id="spot-migration",
        owner_revision="spot-migration-v1",
    )


def _instance() -> RuntimeInstanceView:
    return RuntimeInstanceView(
        instance_id="runtime-1",
        instance_kind="freqtrade-bot",
        owner_ref=_owner(),
        management_mode=RuntimeManagementMode.SUPERVISOR,
        runtime_spec_revision_id="runtime-spec-v1",
        environment="paper",
        state_allocation_id="state-1",
        desired_state=RuntimeDesiredState.STOPPED,
        lifecycle_status=RuntimeLifecycleStatus.REGISTERED,
        failure_latched=False,
        optimistic_version=0,
        created_at=NOW,
        retired_at=None,
    )


def _attempt() -> RuntimeAttemptView:
    return RuntimeAttemptView(
        attempt_id="attempt-1",
        instance_id="runtime-1",
        attempt_number=1,
        runtime_spec_revision_id="runtime-spec-v1",
        adapter_template_revision_id="template-v1",
        status=RuntimeAttemptStatus.PENDING,
        health_result=None,
        started_at=None,
        stopped_at=None,
        exit_code=None,
        failure_code=None,
    )


def _job() -> RuntimeJobView:
    return RuntimeJobView(
        job_id="job-1",
        instance_id="runtime-1",
        requested_action=RuntimeAction.START,
        idempotency_key="operator-20260712-start-1",
        expected_instance_version=0,
        status=RuntimeJobStatus.PENDING,
        lease_owner=None,
        lease_expires_at=None,
        requested_at=NOW,
        started_at=None,
        completed_at=None,
        failure_code=None,
    )


def _command() -> RuntimeLifecycleCommand:
    return RuntimeLifecycleCommand(
        instance_id="runtime-1",
        action=RuntimeAction.START,
        idempotency_key="operator-20260712-start-1",
        expected_instance_version=3,
    )


@pytest.mark.parametrize(
    ("enum_type", "expected_values"),
    [
        (RuntimeOwnerKind, ("migration_bot", "paper_probe", "workspace_worker")),
        (RuntimeManagementMode, ("supervisor",)),
        (RuntimeDesiredState, ("stopped", "running", "retired")),
        (
            RuntimeLifecycleStatus,
            (
                "registered",
                "provisioning",
                "stopped",
                "starting",
                "healthy",
                "stopping",
                "failed",
                "retired",
            ),
        ),
        (
            RuntimeAttemptStatus,
            (
                "pending",
                "validating",
                "launching",
                "healthy",
                "stopping",
                "stopped",
                "failed",
            ),
        ),
        (RuntimeAction, ("start", "stop", "retry", "retire")),
        (
            RuntimeJobStatus,
            (
                "pending",
                "claimed",
                "running",
                "succeeded",
                "failed",
                "needs_reconciliation",
            ),
        ),
    ],
)
def test_runtime_enums_have_exact_closed_values(enum_type, expected_values) -> None:
    assert tuple(item.value for item in enum_type) == expected_values
    with pytest.raises(ValueError):
        enum_type("unknown")


@pytest.mark.parametrize(
    "public_name",
    [
        "RuntimeAction",
        "RuntimeAttemptStatus",
        "RuntimeAttemptView",
        "RuntimeDesiredState",
        "RuntimeInstanceView",
        "RuntimeJobStatus",
        "RuntimeJobView",
        "RuntimeLifecycleCommand",
        "RuntimeLifecycleStatus",
        "RuntimeManagementMode",
        "RuntimeOwnerKind",
        "RuntimeOwnerRef",
    ],
)
def test_runtime_contracts_are_exported_from_platform(public_name: str) -> None:
    assert public_name in platform.__all__
    assert getattr(platform, public_name) is globals()[public_name]


def test_owner_ref_is_closed_and_immutable() -> None:
    owner = _owner()

    with pytest.raises(ValidationError):
        owner.owner_id = "other"
    with pytest.raises(ValidationError):
        RuntimeOwnerRef(owner_kind="unknown", owner_id="x", owner_revision="v1")


@pytest.mark.parametrize("owner_id", ["_owner", "Owner", "x" * 129])
def test_owner_ref_rejects_invalid_identifiers(owner_id: str) -> None:
    with pytest.raises(ValidationError):
        RuntimeOwnerRef(
            owner_kind=RuntimeOwnerKind.PAPER_PROBE,
            owner_id=owner_id,
            owner_revision="v1",
        )


def test_lifecycle_command_is_exact_frozen_and_versioned() -> None:
    command = _command()

    assert command.expected_instance_version == 3
    assert set(RuntimeLifecycleCommand.model_fields) == {
        "instance_id",
        "action",
        "idempotency_key",
        "expected_instance_version",
    }
    with pytest.raises(ValidationError):
        command.action = RuntimeAction.STOP
    with pytest.raises(ValidationError):
        RuntimeLifecycleCommand(
            instance_id="runtime-1",
            action="unknown",
            idempotency_key="key-1",
            expected_instance_version=0,
        )
    with pytest.raises(ValidationError):
        RuntimeLifecycleCommand(
            instance_id="runtime-1",
            action=RuntimeAction.START,
            idempotency_key="key-1",
            expected_instance_version=-1,
        )
    with pytest.raises(ValidationError):
        RuntimeLifecycleCommand(
            instance_id="runtime-1",
            action=RuntimeAction.START,
            idempotency_key="key-1",
            expected_instance_version=0,
            raw_arguments={"service": "arbitrary"},
        )


def test_instance_view_has_exact_closed_summary() -> None:
    instance = _instance()

    assert set(RuntimeInstanceView.model_fields) == {
        "instance_id",
        "instance_kind",
        "owner_ref",
        "management_mode",
        "runtime_spec_revision_id",
        "environment",
        "state_allocation_id",
        "desired_state",
        "lifecycle_status",
        "failure_latched",
        "optimistic_version",
        "created_at",
        "retired_at",
    }
    with pytest.raises(ValidationError):
        instance.lifecycle_status = RuntimeLifecycleStatus.HEALTHY
    for field, value in (
        ("management_mode", "unknown"),
        ("desired_state", "unknown"),
        ("lifecycle_status", "unknown"),
        ("environment", "staging"),
    ):
        with pytest.raises(ValidationError):
            RuntimeInstanceView.model_validate(
                {**instance.model_dump(), field: value},
            )
    with pytest.raises(ValidationError):
        RuntimeInstanceView.model_validate(
            {**instance.model_dump(), "optimistic_version": -1},
        )
    with pytest.raises(ValidationError):
        RuntimeInstanceView.model_validate(
            {**instance.model_dump(), "created_at": datetime(2026, 7, 12)},
        )


def test_attempt_view_has_exact_closed_summary() -> None:
    attempt = _attempt()

    assert set(RuntimeAttemptView.model_fields) == {
        "attempt_id",
        "instance_id",
        "attempt_number",
        "runtime_spec_revision_id",
        "adapter_template_revision_id",
        "status",
        "health_result",
        "started_at",
        "stopped_at",
        "exit_code",
        "failure_code",
    }
    with pytest.raises(ValidationError):
        attempt.status = RuntimeAttemptStatus.HEALTHY
    with pytest.raises(ValidationError):
        RuntimeAttemptView.model_validate({**attempt.model_dump(), "status": "unknown"})
    with pytest.raises(ValidationError):
        RuntimeAttemptView.model_validate({**attempt.model_dump(), "attempt_number": 0})
    with pytest.raises(ValidationError):
        RuntimeAttemptView.model_validate(
            {**attempt.model_dump(), "secret_versions": {"exchange": "v1"}},
        )


def test_job_view_has_exact_closed_summary_and_reconciliation_status() -> None:
    job = _job()

    assert set(RuntimeJobView.model_fields) == {
        "job_id",
        "instance_id",
        "requested_action",
        "idempotency_key",
        "expected_instance_version",
        "status",
        "lease_owner",
        "lease_expires_at",
        "requested_at",
        "started_at",
        "completed_at",
        "failure_code",
    }
    assert (
        RuntimeJobView.model_validate(
            {**job.model_dump(), "status": "needs_reconciliation"},
        ).status
        is RuntimeJobStatus.NEEDS_RECONCILIATION
    )
    with pytest.raises(ValidationError):
        job.status = RuntimeJobStatus.RUNNING
    with pytest.raises(ValidationError):
        RuntimeJobView.model_validate({**job.model_dump(), "status": "unknown"})
    with pytest.raises(ValidationError):
        RuntimeJobView.model_validate(
            {**job.model_dump(), "lease_expires_at": datetime(2026, 7, 12)},
        )
    with pytest.raises(ValidationError):
        RuntimeJobView.model_validate({**job.model_dump(), "payload": {"raw": True}})
