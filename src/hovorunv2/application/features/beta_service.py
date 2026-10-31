"""Admin-controlled beta eligibility; trust and selected features remain independent."""

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.beta import BetaEligibilityFacts, BetaEntitlementStore
from hovorunv2.application.features.registry import FeatureChannel, FeatureRegistry, FeatureScope
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.beta import ALL_BETA


class BetaEligibilityService:
    """Validate registry identities without mirroring grants into the policy engine."""

    def __init__(self, store: BetaEntitlementStore, registry: FeatureRegistry) -> None:
        self._store = store
        self._registry = registry

    async def facts(self, target: PermissionTarget, feature: str) -> BetaEligibilityFacts:
        """Return typed eligibility facts after validating the target scope.

        Facts never enable a feature on their own; callers combine them with the
        registry definition before acting.

        Args:
            target: User or group whose eligibility is being read.
            feature: Feature key to resolve.

        Returns:
            Current eligibility facts for the target and feature.
        """
        self._validate_scope(target, feature)
        return await self._store.facts(target, feature)

    async def is_eligible(self, target: PermissionTarget, feature: str) -> bool:
        """Return whether the target is eligible for the feature.

        All-beta grants are resolved against the current registry definition,
        including features introduced after the grant.

        Args:
            target: User or group being evaluated.
            feature: Feature key to evaluate.

        Returns:
            True if the target is eligible for the feature.
        """
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
        """Change a beta grant through the CLI or a current bot administrator.

        All-beta grants apply only to users; feature grants require a beta feature.

        Args:
            target: User or group receiving the grant.
            feature: Feature key to grant or revoke.
            granted: True to grant, False to revoke.
            actor: Authenticated actor performing the change.
            transport_kind: Transport that initiated the change, if any.

        Returns:
            True if the durable grant state changed.

        Raises:
            ValueError: If an all-beta grant targets a non-user, or the feature is not beta-scoped.
        """
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
        """Validate that the feature supports the target scope.

        Args:
            target: User or group to validate.
            feature: Feature key to validate against.

        Raises:
            ValueError: If the target is not a user or group, or the feature does not support the target scope.
        """
        definition = self._registry.require(feature)
        if target.kind not in {"user", "group"}:
            msg = "Eligibility target must be a user or group."
            raise ValueError(msg)
        # User eligibility may underpin group activation without allowing private execution.
        if target.kind == "group" and FeatureScope.GROUP not in definition.scopes:
            msg = "Feature does not support this target scope."
            raise ValueError(msg)
