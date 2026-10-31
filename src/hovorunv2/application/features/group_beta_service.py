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
        definition = self._registry.require(feature)
        if target.kind != "group" or FeatureScope.GROUP not in definition.scopes:
            msg = "Group beta activation requires a group-scoped feature and target."
            raise ValueError(msg)
        if definition.channel != FeatureChannel.BETA:
            msg = "Group beta activation requires a beta feature."
            raise ValueError(msg)

    def _now(self, now: datetime | None) -> datetime:
        current = now if now is not None else datetime.now(UTC)
        if current.utcoffset() is None:
            msg = "Beta evaluation requires an aware timestamp."
            raise ValueError(msg)
        return current

    async def facts(self, target: PermissionTarget, feature: str, now: datetime | None = None) -> GroupBetaFacts:
        """Read current feature support; no per-member beta entitlement is required."""
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
        """Compose beta support with live group access and an explicit emergency guard."""
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
        """Select a beta feature explicitly; eligible local admins add their own support."""
        self._validate(target, feature)
        return await self._store.set_enabled(
            target,
            feature,
            enabled=enabled,
            actor=actor,
            transport_kind=transport_kind,
        )
