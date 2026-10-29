"""Typed beta eligibility facts and an audited entitlement persistence boundary."""

from dataclasses import dataclass
from typing import Protocol

from hovorunv2.application.features import FeatureChannel, FeatureDefinition
from hovorunv2.application.permissions import PermissionTarget
from hovorunv2.domain.audit import ActorContext


@dataclass(frozen=True, slots=True)
class BetaEligibilityFacts:
    """Current entitlement facts for the shared feature evaluator; never activation state."""

    feature: str
    subject_active: bool
    all_beta: bool = False
    specific_feature: bool = False

    def permits(self, feature: FeatureDefinition) -> bool:
        """Require an active subject and explicit beta entitlement; allow standard features without grants."""
        if not self.subject_active:
            return False
        if self.feature != feature.key:
            return False
        return feature.channel == FeatureChannel.STANDARD or self.all_beta or self.specific_feature


class BetaEntitlementStore(Protocol):
    """Persist lifecycle-bound grants with current authority checks and atomic audit."""

    async def facts(self, target: PermissionTarget, feature: str) -> BetaEligibilityFacts:
        """Read current active identity and nonrevoked, nondeleted entitlements."""
        ...

    async def set_grant(
        self,
        target: PermissionTarget,
        feature: str,
        *,
        granted: bool,
        actor: ActorContext,
        transport_kind: str | None = None,
    ) -> bool:
        """Grant/revoke one entitlement; return whether durable state changed."""
        ...
