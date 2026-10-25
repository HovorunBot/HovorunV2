"""Application service for unified access control and permissions."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hovorunv2.application.permissions import AccessDecision, PermissionRequest
from hovorunv2.application.ports import AppSettings
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.permission_service import PermissionService

if TYPE_CHECKING:
    from aiogram import Bot


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """Declarative policy configuration for command authorization."""

    requires_admin: bool = False
    requires_group_admin: bool = False
    requires_approval: bool = True
    is_toggleable: bool = True


class AccessService:
    """Service to handle cross-platform access control logic.

    Centralizes rules for 'Who can do what' across different messaging platforms.
    """

    def __init__(
        self,
        settings: AppSettings,
        chat_status_service: ChatStatusService,
        command_service: CommandService,
        permission_service: PermissionService,
    ) -> None:
        """Initialize service with dependencies."""
        self._settings = settings
        self._chat_status_service = chat_status_service
        self._command_service = command_service
        self._permission_service = permission_service

    async def evaluate_permission(self, request: PermissionRequest) -> AccessDecision:
        """Evaluate explicit permissions; legacy handlers retain their existing path until cutover."""
        return await self._permission_service.evaluate(request)

    async def is_owner(self, user_id: int) -> bool:
        """Check if a user is an Owner (super admin)."""
        return user_id in self._settings.owners

    async def is_admin(self, user_id: int) -> bool:
        """Check if a user has administrative privileges."""
        return user_id in self._settings.admin_ids or await self.is_owner(user_id)

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
        else:
            return member.status in ("administrator", "creator")

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
        # 1. Feature toggles apply to everyone, including admins.
        if (
            policy.is_toggleable
            and command_name
            and not await self._command_service.is_command_allowed(chat_id, command_name)
        ):
            return False

        is_admin = user_id is not None and await self.is_admin(user_id)

        # 2. Global Admin always has full access
        if is_admin:
            return True

        # 3. Check if global admin is required
        if policy.requires_admin:
            return False

        # 4. Group Admin check
        if policy.requires_group_admin:
            if not user_id or not bot:
                return False
            if not await self.is_group_admin(bot, chat_id, user_id):
                return False

        # 5. Approval check
        return not (policy.requires_approval and not await self._chat_status_service.is_approved(chat_id))
