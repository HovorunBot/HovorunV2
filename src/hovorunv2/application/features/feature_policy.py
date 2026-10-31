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
    """Only automatic group execution may enter without a current unrestricted actor."""
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
    """Automatic group work uses group authority; human actions require a current actor."""
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
    """Apply existing private/group rules independently of fact-loading I/O."""
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
    """Keep management visibility, group access and execution capability as distinct decisions."""
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
