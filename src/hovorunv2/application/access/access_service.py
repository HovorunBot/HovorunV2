"""Application service for unified access control and permissions."""

import logging
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
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.enums import TargetKind

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


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
        """Return whether the user has an active personal or full ban.

        Args:
            user_id: Numeric Telegram identity of the user, or None for an unknown actor.

        Returns:
            Whether any active user restriction exists.
        """
        if user_id is None:
            return False
        bans = await self._ban_store.list_active(TargetKind.USER, user_id)
        return len(bans) > 0

    async def is_trusted(self, user_id: int | None) -> bool:
        """Return whether the user is trusted and still active.

        Args:
            user_id: Numeric Telegram identity of the user, or None for an unknown actor.

        Returns:
            Whether the user exists, carries the trust flag and is not deleted.
        """
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
        """Return whether the user is an administrator or creator in the group chat.

        Args:
            bot: Telegram bot used to query chat membership.
            chat_id: Numeric Telegram identity of the group chat.
            user_id: Numeric Telegram identity of the user.

        Returns:
            Whether the member status is administrator or creator; failures return False.
        """
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            logger.exception("Failed to check chat member status for user %s in chat %s", user_id, chat_id)
            return False
        return member.status in ("administrator", "creator")

    def _group_blocker_decision(self, decision: AccessDecision, policy: CommandPolicy) -> AccessDecision | None:
        """Return a blocking group decision when group bans or approval blockers apply."""
        if decision.reason in {DecisionReason.GROUP_BANNED, DecisionReason.ENTITY_UNAVAILABLE}:
            return decision
        if policy.requires_approval and decision.reason in {
            DecisionReason.ADMIN_FULL_BANNED,
            DecisionReason.MEMBER_EVIDENCE_UNKNOWN,
        }:
            return decision
        return None

    async def _check_non_admin_decision(
        self,
        user_id: int | None,
        chat_id: int,
        policy: CommandPolicy,
        bot: Bot | None,
        group_decision: AccessDecision | None,
    ) -> AccessDecision:
        """Return the access decision for a non-admin actor under the policy constraints."""
        if policy.requires_admin:
            return AccessDecision(allowed=False, reason=DecisionReason.UNAUTHORIZED_MANAGEMENT)
        if policy.requires_group_admin and (
            not user_id or not bot or not await self.is_group_admin(bot, chat_id, user_id)
        ):
            return AccessDecision(allowed=False, reason=DecisionReason.UNAUTHORIZED_MANAGEMENT)
        if policy.requires_approval:
            if group_decision is not None:
                if not group_decision.allowed:
                    return group_decision
            elif not await self._chat_status_service.is_approved(chat_id):
                return AccessDecision(allowed=False, reason=DecisionReason.NOT_APPROVED)
        return AccessDecision(allowed=True, reason=DecisionReason.ALLOWED)

    async def evaluate_command_access(
        self,
        user_id: int | None,
        chat_id: int,
        policy: CommandPolicy,
        bot: Bot | None = None,
        command_name: str | None = None,
    ) -> AccessDecision:
        """Evaluate command access and return an explicit decision with reason.

        Args:
            user_id: Platform-specific user ID.
            chat_id: Platform-specific chat or group ID.
            policy: Command policy to validate against.
            bot: Bot instance required for group administrator checks.
            command_name: Optional name of the command being executed.

        Returns:
            Access decision with the allowing or first blocking reason.
        """
        decision = await self._evaluate_command_access(user_id, chat_id, policy, bot, command_name)
        logger.debug(
            "Access policy evaluated: command=%s chat=%d actor=%s allowed=%s reason=%s",
            command_name or "none",
            chat_id,
            user_id if user_id is not None else "none",
            decision.allowed,
            decision.reason,
        )
        return decision

    async def is_allowed(
        self,
        user_id: int | None,
        chat_id: int,
        policy: CommandPolicy,
        bot: Bot | None = None,
        command_name: str | None = None,
    ) -> bool:
        """Return whether the action is allowed under the given policy.

        Args:
            user_id: Platform-specific user ID.
            chat_id: Platform-specific chat or group ID.
            policy: Command policy to validate against.
            bot: Bot instance required for group administrator checks.
            command_name: Optional name of the command being executed.

        Returns:
            Whether the action is allowed.
        """
        decision = await self.evaluate_command_access(user_id, chat_id, policy, bot, command_name)
        return decision.allowed

    async def _evaluate_command_access(
        self,
        user_id: int | None,
        chat_id: int,
        policy: CommandPolicy,
        bot: Bot | None,
        command_name: str | None,
    ) -> AccessDecision:
        """Evaluate toggles, blockers, bans and authority in a single pass."""
        if (
            policy.is_toggleable
            and command_name
            and not await self._command_service.is_command_allowed(chat_id, command_name)
        ):
            return AccessDecision(allowed=False, reason=DecisionReason.NOT_APPROVED)

        group_decision: AccessDecision | None = None
        if chat_id < 0:
            group_decision = await self._chat_status_service.evaluate_access(chat_id)
            blocker = self._group_blocker_decision(group_decision, policy)
            if blocker is not None:
                return blocker

        # Personal user bans block direct commands, but not automatic media links in groups
        if user_id is not None and await self.is_banned(user_id) and not policy.is_automatic:
            return AccessDecision(allowed=False, reason=DecisionReason.UNAUTHORIZED_MANAGEMENT)

        if user_id is not None and await self.is_admin(user_id):
            return AccessDecision(allowed=True, reason=DecisionReason.ALLOWED)

        return await self._check_non_admin_decision(user_id, chat_id, policy, bot, group_decision)
