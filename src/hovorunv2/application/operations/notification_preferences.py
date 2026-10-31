"""Category-specific notification preferences with enabled defaults and current authority."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ActorOrigin, TargetKind

MESSAGE = "Notification preferences require current subject authority."
AUTHORITY_ACTION = "bot:administer"


class NotificationCategory(StrEnum):
    """Routine broadcast categories; request progress and confirmations stay outside them."""

    RELEASE = "release"
    BETA = "beta"
    FEATURE_STATUS = "feature_status"
    ANNOUNCEMENT = "announcement"


@dataclass(frozen=True, slots=True)
class NotificationPreference:
    """One explicit category switch for a stable subject."""

    category: NotificationCategory
    enabled: bool


class NotificationPreferenceStore(Protocol):
    """Lifecycle-filtered overrides with an audited mutation boundary."""

    async def list(self, target: PermissionTarget) -> tuple[NotificationPreference, ...]:
        """Return active overrides for the exact target generation."""
        ...

    async def set(
        self,
        target: PermissionTarget,
        category: NotificationCategory,
        *,
        enabled: bool,
        actor: ActorContext,
    ) -> bool:
        """Write one explicit switch atomically with audit; unchanged values are a no-op."""
        ...


class NotificationPreferenceService:
    """Resolve enabled defaults and re-check current subject authority before each mutation."""

    def __init__(
        self,
        store: NotificationPreferenceStore,
        users: UserStore,
        groups: GroupManagementService,
        bans: BanStore,
        permissions: PermissionService,
    ) -> None:
        self._store = store
        self._users = users
        self._groups = groups
        self._bans = bans
        self._permissions = permissions

    async def own_target(self, actor_id: int) -> PermissionTarget:
        """Resolve the actor's stable user target without inventing a private chat.

        Args:
            actor_id: Telegram ID of the actor.

        Returns:
            Permission target bound to the actor's current user generation.

        Raises:
            ValueError: If the actor has no active user record.
        """
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None:
            msg = "Notification preferences require an active user."
            raise ValueError(msg)
        return PermissionTarget(TargetKind.USER.value, user.id, user.generation, actor_id)

    async def preferences(self, target: PermissionTarget) -> tuple[NotificationPreference, ...]:
        """Merge explicit overrides over the four enabled defaults.

        Args:
            target: Permission target whose overrides are read.

        Returns:
            Ordered preferences with one resolved switch per category.
        """
        resolved = dict.fromkeys(NotificationCategory, True)
        for preference in await self._store.list(target):
            resolved[preference.category] = preference.enabled
        return tuple(NotificationPreference(category, resolved[category]) for category in NotificationCategory)

    async def resolve(self, target: PermissionTarget, category: NotificationCategory) -> bool:
        """Read one effective category switch for a delivery decision.

        Args:
            target: Permission target whose preferences are read.
            category: Category to resolve.

        Returns:
            True when the category is enabled, False otherwise.
        """
        preferences = await self.preferences(target)
        return next(preference.enabled for preference in preferences if preference.category == category)

    async def set_preference(
        self,
        target: PermissionTarget,
        category: NotificationCategory,
        *,
        enabled: bool,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> bool:
        """Re-check current subject authority, then persist one audited switch.

        Args:
            target: Permission target to update.
            category: Category being switched.
            enabled: New switch value.
            actor: Authenticated caller for the audit.
            transport_kind: Transport of the caller, or None for non-Telegram origins.

        Returns:
            True when the store reported a change, False for a no-op.
        """
        await self._authorize(target, actor, transport_kind)
        return await self._store.set(target, category, enabled=enabled, actor=actor)

    async def _authorize(self, target: PermissionTarget, actor: ActorContext, transport_kind: str | None) -> None:
        """Allow own private preferences, current group managers, or a current bot administrator.

        Args:
            target: Permission target being mutated.
            actor: Authenticated caller.
            transport_kind: Transport of the caller, or None for non-Telegram origins.

        Raises:
            PermissionError: If the caller lacks authority for the target.
        """
        if actor.origin == ActorOrigin.CLI:
            return
        if actor.origin != ActorOrigin.TELEGRAM or actor.actor_id is None or transport_kind is None:
            raise PermissionError(MESSAGE)
        if await self._bans.list_active(TargetKind.USER, actor.actor_id):
            raise PermissionError(MESSAGE)
        if (
            target.kind == TargetKind.USER.value
            and actor.actor_id == target.telegram_id
            and transport_kind == "private"
        ):
            return
        if target.kind == TargetKind.GROUP.value and await self._groups.can_manage(actor.actor_id, target):
            return
        if await self._permissions.has_authority(actor.actor_id, AUTHORITY_ACTION):
            return
        raise PermissionError(MESSAGE)
