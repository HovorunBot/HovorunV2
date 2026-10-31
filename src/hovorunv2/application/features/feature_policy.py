"""Pure feature decisions shared by async ingress and native worker execution."""

from dataclasses import dataclass
from typing import Protocol

from hovorunv2.application.access.permissions import DecisionReason, PermissionTarget
from hovorunv2.application.features.emergency import FeatureEmergencyState
from hovorunv2.application.features.registry import FeatureDefinition, FeatureScope, TriggerKind
from hovorunv2.application.usage.policies import ResolvedUsage, UsageUnit


@dataclass(frozen=True, slots=True)
class FeatureDecision:
    """Safe discovery and execution result; guidance is restricted to managers."""

    visible: bool = False
    allowed: bool = False
    selected: bool = False
    guidance: str | None = None


@dataclass(frozen=True, slots=True)
class FeatureFacts:
    """Fresh typed inputs; these facts are never serialized as an authority grant."""

    scope: FeatureScope
    target_active: bool = False
    actor_active: bool = False
    actor_restricted: bool = False
    private_access: bool = False
    access_reason: DecisionReason = DecisionReason.ENTITY_UNAVAILABLE
    manager: bool = False
    eligible: bool = False
    selected: bool = False
    supported: bool = False
    capable: bool = False
    emergency_disabled: bool = False
    emergency_epoch: int = 0


def _uses_group_authority(scope: FeatureScope, trigger: TriggerKind, *, configure: bool) -> bool:
    """Return whether automatic group execution may proceed without a current actor."""
    return scope == FeatureScope.GROUP and trigger == TriggerKind.AUTOMATIC and not configure


def feature_entry_allowed(
    definition: FeatureDefinition,
    scope: FeatureScope,
    *,
    actor_active: bool,
    actor_restricted: bool,
    trigger: TriggerKind,
    configure: bool,
) -> bool:
    """Return whether the feature may be entered for the given scope and trigger.

    Args:
        definition: Feature definition whose scopes and triggers are checked.
        scope: Scope in which the feature is being entered.
        actor_active: Whether a current actor is present.
        actor_restricted: Whether the current actor is restricted.
        trigger: How the work enters the application.
        configure: True when evaluating configuration rather than execution.

    Returns:
        True if the entry is allowed.
    """
    if scope not in definition.scopes:
        return False
    if not configure and trigger not in definition.triggers:
        return False
    if _uses_group_authority(scope, trigger, configure=configure):
        return True
    return actor_active and not actor_restricted


def evaluate_feature(
    definition: FeatureDefinition, facts: FeatureFacts, *, trigger: TriggerKind, configure: bool = False
) -> FeatureDecision:
    """Apply private and group rules to produce a feature decision.

    Args:
        definition: Feature definition being evaluated.
        facts: Fresh typed facts for the evaluation.
        trigger: How the work enters the application.
        configure: True when evaluating configuration rather than execution.

    Returns:
        The resulting decision, safe by default.
    """
    if facts.emergency_disabled:
        return FeatureDecision()
    if not facts.target_active:
        return FeatureDecision()
    if not feature_entry_allowed(
        definition,
        facts.scope,
        actor_active=facts.actor_active,
        actor_restricted=facts.actor_restricted,
        trigger=trigger,
        configure=configure,
    ):
        return FeatureDecision()
    if facts.scope == FeatureScope.PRIVATE:
        return _private_decision(facts, configure=configure)
    return _group_decision(facts, configure=configure)


def _group_decision(facts: FeatureFacts, *, configure: bool) -> FeatureDecision:
    """Return the group-scoped decision from management, access and capability facts.

    Args:
        facts: Fresh typed facts for the group.
        configure: True when evaluating configuration rather than execution.

    Returns:
        The group decision, with guidance restricted to managers.
    """
    if configure and not facts.manager:
        return FeatureDecision()
    if facts.access_reason != DecisionReason.ALLOWED:
        return FeatureDecision(
            guidance="Group access is unavailable. Check approval and administrator verification."
            if facts.manager
            else None
        )
    if not facts.eligible:
        return FeatureDecision()
    return FeatureDecision(
        visible=configure or (facts.selected and facts.supported and facts.capable),
        allowed=facts.capable and (configure or (facts.selected and facts.supported)),
        selected=facts.selected,
        guidance="Bot permissions are missing or need verification." if facts.manager and not facts.capable else None,
    )


def _private_decision(facts: FeatureFacts, *, configure: bool) -> FeatureDecision:
    """Return the private-scope decision from access and eligibility facts.

    Args:
        facts: Fresh typed facts for the private scope.
        configure: True when evaluating configuration rather than execution.

    Returns:
        The private decision.
    """
    if not facts.private_access or not facts.eligible:
        return FeatureDecision()
    return FeatureDecision(
        visible=configure or (facts.selected and facts.capable),
        allowed=facts.capable and (configure or facts.selected),
        selected=facts.selected,
    )


class SyncPolicyPort(Protocol):
    """Synchronous worker policy evaluation contract."""

    def evaluate(
        self,
        feature: str,
        chat_id: int,
        actor_id: int | None,
        *,
        trigger: TriggerKind = TriggerKind.DIRECT,
        configure: bool = False,
    ) -> FeatureDecision:
        """Apply the bot's exact evaluator and database facts without an event loop."""
        ...

    def emergency_state(self, feature: str) -> FeatureEmergencyState:
        """Read the current authoritative emergency state for stale-envelope fencing."""
        ...

    def disabled_features(self) -> tuple[FeatureEmergencyState, ...]:
        """Return durably disabled features for bounded cleanup retries."""
        ...

    def active_target(self, target: PermissionTarget) -> bool:
        """Validate a queued reference without converting a prior decision into an access grant."""
        ...

    def resolve(
        self,
        feature: str,
        trigger: TriggerKind,
        actor_id: int | None = None,
        *,
        group_id: int | None = None,
        unit: UsageUnit = UsageUnit.REQUESTS,
    ) -> ResolvedUsage:
        """Resolve charged subject and intersect restrictions using the shared pure rule."""
        ...
