"""Security precedence and scope contracts for pure policy evaluation without mocked services."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hovorunv2.application.access.permissions import DecisionReason, GroupAdminEvidence, PermissionTarget
from hovorunv2.application.beta import BetaEligibilityFacts
from hovorunv2.application.feature_policy import FeatureDecision, FeatureFacts, evaluate_feature, feature_entry_allowed
from hovorunv2.application.features import FeatureChannel, FeatureDefinition, FeatureScope, TriggerKind
from hovorunv2.application.group_beta import GroupBetaFacts
from hovorunv2.application.groups.group_policy import GroupAccessFacts, group_access

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TARGET = PermissionTarget("group", 2, 3, -101)
EVIDENCE = GroupAdminEvidence(1, 2, 3, NOW - timedelta(minutes=1), NOW + timedelta(minutes=1))
FEATURE = FeatureDefinition("policy-feature", "Policy feature")
BETA_FEATURE = replace(FEATURE, channel=FeatureChannel.BETA)
GROUP_FACTS = FeatureFacts(
    scope=FeatureScope.GROUP,
    target_active=True,
    actor_active=True,
    access_reason=DecisionReason.ALLOWED,
    manager=True,
    eligible=True,
    selected=True,
    supported=True,
    capable=True,
)


@pytest.mark.parametrize(
    ("overrides", "now", "expected"),
    [
        ({}, NOW, True),
        ({}, EVIDENCE.verified_at, True),
        ({}, EVIDENCE.expires_at, False),
        ({"actor_id": 9}, NOW, False),
        ({"target_id": 9}, NOW, False),
        ({"target_generation": 9}, NOW, False),
        ({"verified_at": NOW + timedelta(seconds=1)}, NOW, False),
        ({"verified_at": EVIDENCE.verified_at.replace(tzinfo=None)}, NOW, False),
        ({"expires_at": EVIDENCE.expires_at.replace(tzinfo=None)}, NOW, False),
        ({"target_id": 9}, NOW.replace(tzinfo=None), False),
    ],
)
def test_admin_evidence_preserves_binding_and_time_boundaries(
    overrides: dict[str, object], now: datetime, expected: bool
) -> None:
    """Mismatched targets short-circuit time checks; valid evidence uses an inclusive start and exclusive expiry."""
    assert replace(EVIDENCE, **overrides).applies_to(1, TARGET, now) is expected


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"active": False, "banned": True}, DecisionReason.ENTITY_UNAVAILABLE),
        ({"banned": True}, DecisionReason.GROUP_BANNED),
        ({"admins_fresh": False, "banned_admin": True}, DecisionReason.MEMBER_EVIDENCE_UNKNOWN),
        ({"unknown_admin": True, "banned_admin": True}, DecisionReason.ADMIN_FULL_BANNED),
        ({"unknown_admin": True}, DecisionReason.MEMBER_EVIDENCE_UNKNOWN),
        ({"banned_admin": True, "approved": False}, DecisionReason.ADMIN_FULL_BANNED),
        ({"approved": False}, DecisionReason.NOT_APPROVED),
        ({}, DecisionReason.ALLOWED),
    ],
)
def test_group_blocker_precedence_is_explicit(overrides: dict[str, bool], reason: DecisionReason) -> None:
    """Stale evidence wins over administrator bans, while fresh proven bans win over unknown peers."""
    facts = replace(GroupAccessFacts(active=True, admins_fresh=True, approved=True), **overrides)
    decision = group_access(facts)
    assert decision.reason == reason
    assert decision.allowed is (reason == DecisionReason.ALLOWED)


@pytest.mark.parametrize(
    ("scope", "trigger", "configure", "actor_active", "actor_restricted", "expected"),
    [
        (FeatureScope.GROUP, TriggerKind.AUTOMATIC, False, False, True, True),
        (FeatureScope.GROUP, TriggerKind.DIRECT, False, False, False, False),
        (FeatureScope.GROUP, TriggerKind.DIRECT, False, True, True, False),
        (FeatureScope.GROUP, TriggerKind.AUTOMATIC, True, False, False, False),
        (FeatureScope.GROUP, TriggerKind.AUTOMATIC, True, True, True, False),
        (FeatureScope.GROUP, TriggerKind.AUTOMATIC, True, True, False, True),
        (FeatureScope.PRIVATE, TriggerKind.AUTOMATIC, False, False, False, False),
        (FeatureScope.PRIVATE, TriggerKind.DIRECT, False, True, False, True),
    ],
)
def test_automatic_group_authority_does_not_extend_to_human_actions(
    scope: FeatureScope,
    trigger: TriggerKind,
    configure: bool,
    actor_active: bool,
    actor_restricted: bool,
    expected: bool,
) -> None:
    """Configuration and private requests retain actor checks even when the trigger is automatic."""
    assert (
        feature_entry_allowed(
            FEATURE,
            scope,
            actor_active=actor_active,
            actor_restricted=actor_restricted,
            trigger=trigger,
            configure=configure,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("overrides", "configure", "expected"),
    [
        ({}, False, FeatureDecision(True, True, True)),
        ({"target_active": False}, False, FeatureDecision()),
        ({"manager": False}, True, FeatureDecision()),
        ({"eligible": False}, False, FeatureDecision()),
        ({"selected": False, "supported": False}, True, FeatureDecision(True, True, False)),
        (
            {"capable": False},
            True,
            FeatureDecision(True, False, True, "Bot permissions are missing or need verification."),
        ),
        (
            {"access_reason": DecisionReason.NOT_APPROVED},
            False,
            FeatureDecision(guidance="Group access is unavailable. Check approval and administrator verification."),
        ),
        ({"access_reason": DecisionReason.NOT_APPROVED, "manager": False}, False, FeatureDecision()),
    ],
)
def test_feature_visibility_and_execution_remain_distinct(
    overrides: dict[str, object], configure: bool, expected: FeatureDecision
) -> None:
    """Managers can configure unsupported selections and see missing-capability guidance without execution access."""
    decision = evaluate_feature(
        FEATURE, replace(GROUP_FACTS, **overrides), trigger=TriggerKind.DIRECT, configure=configure
    )
    assert decision == expected


@pytest.mark.parametrize(
    ("overrides", "definition", "expected"),
    [
        ({}, FEATURE, True),
        ({}, BETA_FEATURE, False),
        ({"all_beta": True}, BETA_FEATURE, True),
        ({"specific_feature": True}, BETA_FEATURE, True),
        ({"subject_active": False, "all_beta": True}, BETA_FEATURE, False),
        ({"feature": "another-feature", "all_beta": True}, BETA_FEATURE, False),
    ],
)
def test_beta_eligibility_does_not_substitute_for_subject_binding(
    overrides: dict[str, object], definition: FeatureDefinition, expected: bool
) -> None:
    """Standard release access and beta entitlements both require the exact active subject/feature facts."""
    facts = replace(BetaEligibilityFacts(FEATURE.key, subject_active=True), **overrides)
    assert facts.permits(definition) is expected


@pytest.mark.parametrize(
    ("overrides", "group_allowed", "emergency_disabled", "expected"),
    [
        ({}, True, False, True),
        ({"selected": False}, True, False, False),
        ({"subject_active": False}, True, False, False),
        ({"group_granted": False}, True, False, False),
        ({"group_granted": False, "qualifying_enabler": True}, True, False, True),
        ({"feature": "another-feature"}, True, False, False),
        ({}, False, False, False),
        ({}, True, True, False),
    ],
)
def test_group_beta_support_cannot_bypass_selection_or_execution_blockers(
    overrides: dict[str, object], group_allowed: bool, emergency_disabled: bool, expected: bool
) -> None:
    """Group grants and current supporting administrators satisfy support, not approval or emergency controls."""
    facts = replace(GroupBetaFacts(FEATURE.key, subject_active=True, selected=True, group_granted=True), **overrides)
    assert facts.permits(BETA_FEATURE, group_allowed=group_allowed, emergency_disabled=emergency_disabled) is expected
