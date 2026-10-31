"""Preserve feature metadata, job binding and administrative request validation contracts."""

import math
from dataclasses import replace

import pytest
from pydantic import ValidationError

from hovorunv2.application.access.administration import AdminAction, AdminRequest
from hovorunv2.application.feature_jobs import MAX_JOB_LIFETIME_SECONDS, JobEnvelope, JobTarget
from hovorunv2.application.features import FeatureChannel, FeatureDefinition, TriggerKind
from hovorunv2.domain.enums import TargetKind

GROUP_TARGET = JobTarget(kind=TargetKind.GROUP, entity_id=1, generation=1, telegram_id=-101)
USER_TARGET = JobTarget(kind=TargetKind.USER, entity_id=2, generation=1, telegram_id=101)
ENVELOPE_DATA: dict[str, object] = {
    "job_id": "contract-job",
    "payload_ref": "a" * 32,
    "feature": "twitter_video",
    "target": GROUP_TARGET,
    "actor_id": 101,
    "actor_generation": 1,
    "trigger": TriggerKind.DIRECT,
    "charged_kind": TargetKind.USER,
    "charged_telegram_id": 101,
    "accepted_at": 100.25,
    "deadline": 100.25 + MAX_JOB_LIFETIME_SECONDS,
    "dedupe_key": "b" * 64,
    "feature_epoch": 0,
    "message_id": 1,
}
FEATURE = FeatureDefinition("contract-feature", "Contract feature")
METADATA_ERROR = "Feature identity, introduction, scopes and triggers must be defined"
POLICY_ERROR = "Feature usage, queue and notification policies must be defined"
BINDING_ERROR = "Job trigger, target and charged subject disagree."
DEADLINE_ERROR = "Feature job requires an original deadline within five minutes of acceptance."


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"key": ""}, METADATA_ERROR),
        ({"display_name": ""}, METADATA_ERROR),
        ({"introduction": ""}, METADATA_ERROR),
        ({"scopes": frozenset()}, METADATA_ERROR),
        ({"triggers": frozenset()}, METADATA_ERROR),
        ({"units": frozenset()}, POLICY_ERROR),
        ({"usage_policy": ""}, POLICY_ERROR),
        ({"queue_policy": ""}, POLICY_ERROR),
        ({"notification_category": ""}, POLICY_ERROR),
        ({"channel": FeatureChannel.BETA, "default_enabled": True}, "Beta features must start disabled"),
        ({"key": "", "units": frozenset()}, METADATA_ERROR),
        ({"units": frozenset(), "channel": FeatureChannel.BETA, "default_enabled": True}, POLICY_ERROR),
    ],
)
def test_feature_requirements_preserve_error_precedence(overrides: dict[str, object], message: str) -> None:
    """Metadata requirements precede policy requirements, which precede release defaults."""
    with pytest.raises(ValueError, match=message) as error:
        replace(FEATURE, **overrides)
    assert str(error.value) == message


@pytest.mark.parametrize(
    ("kind", "telegram_id"),
    [
        (TargetKind.USER, 0),
        (TargetKind.USER, -101),
        (TargetKind.GROUP, 101),
        (TargetKind.USER, 1 << 63),
        (TargetKind.GROUP, -(1 << 63)),
    ],
)
def test_job_target_preserves_identity_constraints(kind: TargetKind, telegram_id: int) -> None:
    """Stable identity range and target sign remain mandatory without changing safe error text."""
    with pytest.raises(ValidationError, match="Job target kind and Telegram identity disagree") as error:
        JobTarget(kind=kind, entity_id=1, generation=1, telegram_id=telegram_id)
    assert error.value.errors(include_input=False, include_context=False)[0]["loc"] == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"deadline": 100.25}, DEADLINE_ERROR),
        ({"deadline": 401.25}, DEADLINE_ERROR),
        ({"deadline": 401.25, "actor_id": None}, DEADLINE_ERROR),
        ({"actor_id": None}, BINDING_ERROR),
        ({"actor_generation": None}, BINDING_ERROR),
        ({"charged_kind": TargetKind.GROUP}, BINDING_ERROR),
        ({"charged_telegram_id": 102}, BINDING_ERROR),
        ({"target": USER_TARGET.model_copy(update={"telegram_id": 102})}, BINDING_ERROR),
        ({"trigger": TriggerKind.AUTOMATIC, "target": USER_TARGET}, BINDING_ERROR),
        ({"trigger": TriggerKind.AUTOMATIC}, BINDING_ERROR),
        (
            {"trigger": TriggerKind.AUTOMATIC, "charged_kind": TargetKind.GROUP, "charged_telegram_id": -102},
            BINDING_ERROR,
        ),
        (
            {
                "trigger": TriggerKind.AUTOMATIC,
                "charged_kind": TargetKind.GROUP,
                "charged_telegram_id": -101,
                "actor_id": None,
            },
            BINDING_ERROR,
        ),
        (
            {
                "trigger": TriggerKind.AUTOMATIC,
                "charged_kind": TargetKind.GROUP,
                "charged_telegram_id": -101,
                "actor_generation": None,
            },
            BINDING_ERROR,
        ),
    ],
)
def test_envelope_rejects_inconsistent_bindings_with_stable_errors(overrides: dict[str, object], message: str) -> None:
    """Deadline failures precede actor, transport and charged-subject validation."""
    with pytest.raises(ValidationError, match=message) as error:
        JobEnvelope.model_validate({**ENVELOPE_DATA, **overrides})
    detail = error.value.errors(include_input=False, include_context=False)[0]
    assert detail["loc"] == ()
    assert detail["msg"] == f"Value error, {message}"


@pytest.mark.parametrize(
    "overrides",
    [
        {"actor_id": 0},
        {"actor_id": -1},
        {"actor_generation": 0},
        {"actor_generation": -1},
        {"actor_generation": math.nan},
    ],
)
def test_explicit_binding_validation_rejects_invalid_copied_actor(overrides: dict[str, object]) -> None:
    """The public validator must still reject invalid identities after an unvalidated model copy."""
    envelope = JobEnvelope.model_validate(ENVELOPE_DATA).model_copy(update=overrides)
    with pytest.raises(ValueError, match=BINDING_ERROR):
        envelope.validate_binding()


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"target": USER_TARGET},
        {"trigger": TriggerKind.AUTOMATIC, "charged_kind": TargetKind.GROUP, "charged_telegram_id": -101},
        {
            "trigger": TriggerKind.AUTOMATIC,
            "charged_kind": TargetKind.GROUP,
            "charged_telegram_id": -101,
            "actor_id": None,
            "actor_generation": None,
        },
    ],
)
def test_valid_envelope_roundtrip_preserves_optional_automatic_actor(overrides: dict[str, object]) -> None:
    """Direct private/group and automatic anonymous/named actors retain the same wire contract."""
    envelope = JobEnvelope.model_validate({**ENVELOPE_DATA, **overrides})
    assert JobEnvelope.model_validate(envelope.model_dump(mode="json")) == envelope
    assert envelope.deadline == 100.25 + MAX_JOB_LIFETIME_SECONDS


@pytest.mark.parametrize(
    ("action", "kind", "request_id", "reason", "message"),
    [
        (AdminAction.TRUST, TargetKind.GROUP, None, None, "Trust applies only to users."),
        (AdminAction.UNTRUST, TargetKind.GROUP, None, None, "Trust applies only to users."),
        (AdminAction.APPROVE, TargetKind.USER, None, None, "Approval and request operations require a group."),
        (AdminAction.ACCEPT, TargetKind.USER, None, None, "Approval and request operations require a group."),
        (AdminAction.ACCEPT, TargetKind.GROUP, None, None, "Request decisions require a positive request ID."),
        (AdminAction.REJECT, TargetKind.GROUP, 0, None, "Request decisions require a positive request ID."),
        (AdminAction.REJECT, TargetKind.GROUP, -1, None, "Request decisions require a positive request ID."),
        (AdminAction.BAN, TargetKind.USER, None, None, "Ban changes require an internal reason."),
        (AdminAction.UNBAN, TargetKind.GROUP, None, " ", "Ban changes require an internal reason."),
    ],
)
def test_admin_request_preserves_operation_validation(
    action: AdminAction, kind: TargetKind, request_id: int | None, reason: str | None, message: str
) -> None:
    """Wrong target scope is rejected before missing request details or internal reasons."""
    with pytest.raises(ValidationError, match=message) as error:
        AdminRequest(action=action, kind=kind, target="101", request_id=request_id, reason=reason)
    assert error.value.errors(include_input=False, include_context=False)[0]["msg"] == f"Value error, {message}"
