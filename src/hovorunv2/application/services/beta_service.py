"""Admin-controlled beta eligibility; trust and selected features remain independent."""

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.beta import BetaEligibilityFacts, BetaEntitlementStore
from hovorunv2.application.features import FeatureChannel, FeatureRegistry, FeatureScope
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.beta import ALL_BETA


class BetaEligibilityService:
    """Validate registry identities without mirroring grants into the policy engine."""

    def __init__(self, store: BetaEntitlementStore, registry: FeatureRegistry) -> None:
        self._store = store
        self._registry = registry

    async def facts(self, target: PermissionTarget, feature: str) -> BetaEligibilityFacts:
        """Expose typed current facts to feature evaluation; this never enables a feature."""
        self._validate_scope(target, feature)
        return await self._store.facts(target, feature)

    async def is_eligible(self, target: PermissionTarget, feature: str) -> bool:
        """Resolve all-beta against current definitions, including later introductions."""
        facts = await self.facts(target, feature)
        return facts.permits(self._registry.require(feature))

    async def set_grant(
        self,
        target: PermissionTarget,
        feature: str,
        *,
        granted: bool,
        actor: ActorContext,
        transport_kind: str | None = None,
    ) -> bool:
        """Change a beta grant through CLI or current bot administrator in private transport."""
        if feature == ALL_BETA:
            if target.kind != "user":
                msg = "All-beta grants apply only to users; groups require a specific feature."
                raise ValueError(msg)
        else:
            self._validate_scope(target, feature)
            if self._registry.require(feature).channel != FeatureChannel.BETA:
                msg = "Eligibility grants require a beta feature."
                raise ValueError(msg)
        return await self._store.set_grant(target, feature, granted=granted, actor=actor, transport_kind=transport_kind)

    def _validate_scope(self, target: PermissionTarget, feature: str) -> None:
        definition = self._registry.require(feature)
        if target.kind not in {"user", "group"}:
            msg = "Eligibility target must be a user or group."
            raise ValueError(msg)
        # User eligibility may underpin group activation without allowing private execution.
        if target.kind == "group" and FeatureScope.GROUP not in definition.scopes:
            msg = "Feature does not support this target scope."
            raise ValueError(msg)
