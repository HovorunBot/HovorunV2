"""Application service for unified access control and permissions."""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import AccessDecision, DecisionReason, PermissionRequest
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.features.feature_access_service import FeatureAccessService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.ports import UserStore
from hovorunv2.domain.enums import TargetKind

if TYPE_CHECKING:
    from aiogram import Bot


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """Declarative policy configuration for command authorization."""

    requires_admin: bool = False
    requires_group_admin: bool = False
    requires_approval: bool = True
    is_toggleable: bool = True
    is_automatic: bool = False


class AccessService:
    """Service to handle cross-platform access control logic.

    Centralizes rules for 'Who can do what' across different messaging platforms.
    """

    def __init__(
        self,
        chat_status_service: ChatStatusService,
        command_service: CommandService,
        permission_service: PermissionService,
        ban_store: BanStore,
        user_store: UserStore,
        membership_store: MembershipStore,
        feature_access: FeatureAccessService,
    ) -> None:
        """Initialize service with dependencies."""
        self._chat_status_service = chat_status_service
        self._command_service = command_service
        self._permission_service = permission_service
        self._ban_store = ban_store
        self._user_store = user_store
        self._membership_store = membership_store
        self.features = feature_access

    async def evaluate_permission(self, request: PermissionRequest) -> AccessDecision:
        """Evaluate explicit action/target guards beside legacy group-policy orchestration."""
        return await self._permission_service.evaluate(request)

    async def evaluate_group_access(self, chat_id: int, now: datetime | None = None) -> AccessDecision:
        """Evaluate durable approval, bans and administrator blockers for a group."""
        return await self._chat_status_service.evaluate_access(chat_id, now=now)

    async def is_owner(self, user_id: int) -> bool:
        """Check if a user is an Owner (super admin)."""
        return await self._permission_service.has_authority(user_id, "bot:own")

    async def is_admin(self, user_id: int) -> bool:
        """Check if a user has administrative privileges."""
        return await self._permission_service.has_authority(user_id, "bot:administer")

    async def is_banned(self, user_id: int | None) -> bool:
        """Check if user has an active personal or full ban."""
        if user_id is None:
            return False
        bans = await self._ban_store.list_active(TargetKind.USER, user_id)
        return len(bans) > 0

    async def is_trusted(self, user_id: int | None) -> bool:
        """Check if user has trusted flag enabled and is active."""
        if user_id is None:
            return False
        user = await self._user_store.get_by_telegram_id(user_id)
        return user is not None and user.is_trusted and user.deleted_at is None

    async def is_group_admin(
        self,
        bot: Bot,
        chat_id: int,
        user_id: int,
    ) -> bool:
        """Check if a user is an administrator in a specific group chat."""
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            return False
        return member.status in ("administrator", "creator")

    async def _group_policy_allows(self, chat_id: int, policy: CommandPolicy) -> bool:
        """Apply group blockers before any actor authority bypass."""
        decision = await self._chat_status_service.evaluate_access(chat_id)
        if decision.reason in {DecisionReason.GROUP_BANNED, DecisionReason.ENTITY_UNAVAILABLE}:
            return False
        return not (
            policy.requires_approval
            and decision.reason in {DecisionReason.ADMIN_FULL_BANNED, DecisionReason.MEMBER_EVIDENCE_UNKNOWN}
        )

    async def _check_non_admin_policy(
        self,
        user_id: int | None,
        chat_id: int,
        policy: CommandPolicy,
        bot: Bot | None,
    ) -> bool:
        """Evaluate policy constraints for non-administrative users."""
        if policy.requires_admin:
            return False
        if policy.requires_group_admin and (
            not user_id or not bot or not await self.is_group_admin(bot, chat_id, user_id)
        ):
            return False
        return not (policy.requires_approval and not await self._chat_status_service.is_approved(chat_id))

    async def is_allowed(
        self,
        user_id: int | None,
        chat_id: int,
        policy: CommandPolicy,
        bot: Bot | None = None,
        command_name: str | None = None,
    ) -> bool:
        """Check if an action is allowed based on the provided policy.

        Args:
            user_id: Platform-specific user ID.
            chat_id: Platform-specific chat/group ID.
            policy: The validation strategy to apply.
            bot: The bot instance (required for group admin checks).
            command_name: Optional name of the command being executed.

        Returns:
            True if allowed, False otherwise.

        """
        if (
            policy.is_toggleable
            and command_name
            and not await self._command_service.is_command_allowed(chat_id, command_name)
        ):
            return False

        # Group bans always block everyone, including global admins.
        if chat_id < 0 and not await self._group_policy_allows(chat_id, policy):
            return False

        # Personal user bans block direct commands, but not automatic media links in groups
        if user_id is not None and await self.is_banned(user_id) and not policy.is_automatic:
            return False

        if user_id is not None and await self.is_admin(user_id):
            return True

        return await self._check_non_admin_policy(user_id, chat_id, policy, bot)
