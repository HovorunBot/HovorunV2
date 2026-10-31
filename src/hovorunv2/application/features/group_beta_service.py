"""Whole-group beta activation independent of trust and durable group approval."""

from datetime import UTC, datetime

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.group_beta import GroupBetaFacts, GroupBetaStore
from hovorunv2.application.features.registry import FeatureChannel, FeatureRegistry, FeatureScope
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.domain.audit import ActorContext


class GroupBetaService:
    """Keep selected state durable while deriving availability from current support."""

    def __init__(self, store: GroupBetaStore, registry: FeatureRegistry, chats: ChatStatusService) -> None:
        self._store = store
        self._registry = registry
        self._chats = chats

    def _validate(self, target: PermissionTarget, feature: str) -> None:
        """Validate that the target and feature support group beta activation.

        Args:
            target: Group target to validate.
            feature: Feature key to validate.

        Raises:
            ValueError: If the target is not a group, or the feature is not group-scoped or beta.
        """
        definition = self._registry.require(feature)
        if target.kind != "group" or FeatureScope.GROUP not in definition.scopes:
            msg = "Group beta activation requires a group-scoped feature and target."
            raise ValueError(msg)
        if definition.channel != FeatureChannel.BETA:
            msg = "Group beta activation requires a beta feature."
            raise ValueError(msg)

    def _now(self, now: datetime | None) -> datetime:
        """Return the supplied timestamp or the current UTC time, requiring awareness.

        Args:
            now: Timestamp to use, or None for the current time.

        Returns:
            The aware timestamp.

        Raises:
            ValueError: If the supplied timestamp is naive.
        """
        current = now if now is not None else datetime.now(UTC)
        if current.utcoffset() is None:
            msg = "Beta evaluation requires an aware timestamp."
            raise ValueError(msg)
        return current

    async def facts(self, target: PermissionTarget, feature: str, now: datetime | None = None) -> GroupBetaFacts:
        """Return current group beta support facts for the target.

        Args:
            target: Group whose beta support is read.
            feature: Beta feature key to resolve.
            now: Optional timestamp, defaulting to the current UTC time.

        Returns:
            Current feature-bound selection and support facts.
        """
        self._validate(target, feature)
        return await self._store.facts(target, feature, self._now(now))

    async def is_available(
        self,
        target: PermissionTarget,
        feature: str,
        *,
        emergency_disabled: bool,
        now: datetime | None = None,
    ) -> bool:
        """Return whether group beta activation is currently available.

        Args:
            target: Group whose availability is evaluated.
            feature: Beta feature key to evaluate.
            emergency_disabled: Whether the feature is currently emergency-disabled.
            now: Optional timestamp, defaulting to the current UTC time.

        Returns:
            True if beta support, live group access and the emergency guard all pass.
        """
        current = self._now(now)
        facts = await self.facts(target, feature, current)
        if not facts.subject_active or target.telegram_id is None:
            return False
        access = await self._chats.evaluate_access(target.telegram_id, now=current)
        return facts.permits(
            self._registry.require(feature), group_allowed=access.allowed, emergency_disabled=emergency_disabled
        )

    async def set_enabled(
        self,
        target: PermissionTarget,
        feature: str,
        *,
        enabled: bool,
        actor: ActorContext,
        transport_kind: str | None = None,
    ) -> bool:
        """Select or deselect a beta feature for the group.

        Args:
            target: Group whose beta selection changes.
            feature: Beta feature key to select.
            enabled: True to select, False to deselect.
            actor: Authenticated actor performing the change.
            transport_kind: Transport that initiated the change, if any.

        Returns:
            True if the durable selection changed.
        """
        self._validate(target, feature)
        return await self._store.set_enabled(
            target,
            feature,
            enabled=enabled,
            actor=actor,
            transport_kind=transport_kind,
        )
