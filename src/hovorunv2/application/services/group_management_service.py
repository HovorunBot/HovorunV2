"""Private group discovery and current target-specific management authorization."""

from dataclasses import dataclass
from typing import Final

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.membership import MembershipPort, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import GroupAdminEvidence, PermissionRequest, PermissionTarget
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.permission_service import PermissionService
from hovorunv2.domain.enums import TargetKind

GROUPS_PER_PAGE: Final = 8


@dataclass(frozen=True, slots=True)
class ManagedGroup:
    """Only authorized operational group metadata is returned to private discovery."""

    entity_id: int
    generation: int
    telegram_id: int
    title: str | None


@dataclass(frozen=True, slots=True)
class ManagedGroupPage:
    """A bounded candidate page may contain no authorized groups and still have a next page."""

    groups: tuple[ManagedGroup, ...]
    next_after: int | None


class GroupManagementService:
    """Never infer management authority from trust, a list entry or an old menu."""

    def __init__(
        self,
        chats: ChatStore,
        users: UserStore,
        bans: BanStore,
        memberships: MembershipStore,
        telegram: MembershipPort,
        permissions: PermissionService,
    ) -> None:
        self._chats = chats
        self._users = users
        self._bans = bans
        self._memberships = memberships
        self._telegram = telegram
        self._permissions = permissions

    async def _actor_available(self, actor_id: int) -> bool:
        return await self._users.get_by_telegram_id(actor_id) is not None and not await self._bans.list_active(
            TargetKind.USER, actor_id
        )

    async def can_manage(self, actor_id: int, target: PermissionTarget) -> bool:
        """Verify current actor, exact target/generation and live Telegram authority on each action."""
        if target.kind != "group" or target.telegram_id is None or not await self._actor_available(actor_id):
            return False
        group = await self._chats.get_chat(target.telegram_id)
        if (
            group is None
            or group.id != target.entity_id
            or group.generation != target.generation
            or group.chat_id != target.telegram_id
            or group.chat_id >= 0
        ):
            return False
        # Resource suspension does not remove current bot administrators' private inspection authority.
        if await self._permissions.has_authority(actor_id, "bot:administer"):
            return True
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None or not group.is_bot_present:
            return False
        evidence = await self._telegram.verify_member(group.chat_id, actor_id)
        await self._memberships.save_member(group.id, evidence, expected_user_id=user.id)
        current_group = await self._chats.get_chat(target.telegram_id)
        current_user = await self._users.get_by_telegram_id(actor_id)
        if (
            current_group is None
            or current_group.id != target.entity_id
            or current_group.generation != target.generation
            or current_group.chat_id != target.telegram_id
            or not current_group.is_bot_present
            or current_user is None
            or current_user.id != user.id
            or not await self._actor_available(actor_id)
            or evidence.status != MembershipStatus.ADMIN
            or evidence.group_telegram_id != target.telegram_id
            or evidence.user_telegram_id != actor_id
        ):
            return False
        verified = GroupAdminEvidence(actor_id, group.id, group.generation, evidence.verified_at, evidence.expires_at)
        return bool(
            await self._permissions.evaluate(
                PermissionRequest(
                    actor_id,
                    "group:settings",
                    target,
                    group_admin=verified,
                    transport_kind="private",
                )
            )
        )

    async def list_groups(self, actor_id: int, after_id: int = 0) -> ManagedGroupPage:
        """Scan one bounded page; reveal only currently authorized groups, never unrelated titles."""
        if after_id < 0 or not await self._actor_available(actor_id):
            return ManagedGroupPage((), None)
        bot_admin = await self._permissions.has_authority(actor_id, "bot:administer")
        candidates = await self._chats.list_groups(after_id, GROUPS_PER_PAGE + 1, present_only=not bot_admin)
        groups: list[ManagedGroup] = []
        for candidate in candidates[:GROUPS_PER_PAGE]:
            target = PermissionTarget("group", candidate.id, candidate.generation, candidate.chat_id)
            if await self.can_manage(actor_id, target):
                groups.append(ManagedGroup(candidate.id, candidate.generation, candidate.chat_id, candidate.title))
        next_after = candidates[GROUPS_PER_PAGE - 1].id if len(candidates) > GROUPS_PER_PAGE else None
        return ManagedGroupPage(tuple(groups), next_after)
