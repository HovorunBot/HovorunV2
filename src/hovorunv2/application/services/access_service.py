"""Application service for unified access control and permissions."""

from typing import TYPE_CHECKING

from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.config import Settings

if TYPE_CHECKING:
    from aiogram import Bot


class CommandPolicy:
    """Strategy for validating command execution.

    Allows for declarative validation rules (admin, whitelist, toggleable)
    while supporting complex custom logic by overriding evaluate().
    """

    def __init__(
        self,
        *,
        requires_admin: bool = False,
        requires_group_admin: bool = False,
        requires_whitelist: bool = True,
        is_toggleable: bool = True,
        auto_enable: bool = False,
    ) -> None:
        """Initialize policy with declarative rules."""
        self.requires_admin = requires_admin
        self.requires_group_admin = requires_group_admin
        self.requires_whitelist = requires_whitelist
        self.is_toggleable = is_toggleable
        self.auto_enable = auto_enable

    async def evaluate(
        self,
        user_id: int | None,
        chat_id: int,
        command_name: str | None,
        platform: str,
        access_service: AccessService,
        whitelist_service: WhitelistService,
        command_service: CommandService,
        bot: Bot | None = None,
    ) -> bool:
        """Evaluate access rules.

        Admins/Owners bypass all checks unless specific policy says otherwise.
        """
        # 1. Feature toggles apply to everyone, including admins.
        # If a command is explicitly disabled in this chat, nobody can use it.
        if (
            self.is_toggleable
            and command_name
            and not await command_service.is_command_allowed(chat_id, command_name, platform)
        ):
            return False

        is_admin = user_id and await access_service.is_admin(user_id, platform)

        # 2. Global Admin always has full access
        if is_admin:
            return True

        # 3. Check if global admin is required
        if self.requires_admin:
            return False

        # 4. Group Admin check
        if self.requires_group_admin:
            if not user_id or not bot:
                return False
            # Check if user is admin in this specific group
            if not await access_service.is_group_admin(bot, chat_id, user_id, platform):
                return False

        # 5. Whitelist check
        return not (self.requires_whitelist and not await whitelist_service.is_whitelisted(chat_id, platform))


class AccessService:
    """Service to handle cross-platform access control logic.

    Centralizes rules for 'Who can do what' across different messaging platforms.
    Delegates specific validation logic to CommandPolicy objects.
    """

    def __init__(
        self,
        settings: Settings,
        whitelist_service: WhitelistService,
        command_service: CommandService,
    ) -> None:
        """Initialize service with dependencies."""
        self._settings = settings
        self._whitelist_service = whitelist_service
        self._command_service = command_service

    async def is_owner(self, user_id: int, platform: str = "telegram") -> bool:
        """Check if a user is an Owner (super admin)."""
        match platform:
            case "telegram":
                return user_id in self._settings.owners
            case _:
                return False

    async def is_admin(self, user_id: int, platform: str = "telegram") -> bool:
        """Check if a user has administrative privileges on a specific platform."""
        match platform:
            case "telegram":
                return user_id in self._settings.admin_ids or await self.is_owner(user_id, platform)
            case _:
                return False

    async def is_group_admin(
        self,
        bot: Bot,
        chat_id: int,
        user_id: int,
        platform: str = "telegram",
    ) -> bool:
        """Check if a user is an administrator in a specific group chat."""
        if platform != "telegram":
            return False

        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            return False
        else:
            return member.status in ("administrator", "creator")

    async def ensure_admin(self, user_id: int, platform: str = "telegram") -> None:
        """Verify user is admin or raise an error."""
        if not await self.is_admin(user_id, platform):
            msg = f"User {user_id} on {platform} does not have admin privileges"
            raise PermissionError(msg)

    async def is_allowed(
        self,
        user_id: int | None,
        chat_id: int,
        policy: CommandPolicy,
        bot: Bot | None = None,
        command_name: str | None = None,
        platform: str = "telegram",
    ) -> bool:
        """Check if an action is allowed based on the provided policy.

        Args:
            user_id: Platform-specific user ID.
            chat_id: Platform-specific chat/group ID.
            policy: The validation strategy to apply.
            bot: The bot instance (required for group admin checks).
            command_name: Optional name of the command being executed.
            platform: Messaging platform identifier.

        Returns:
            True if allowed, False otherwise.

        """
        return await policy.evaluate(
            user_id=user_id,
            chat_id=chat_id,
            command_name=command_name,
            platform=platform,
            access_service=self,
            whitelist_service=self._whitelist_service,
            command_service=self._command_service,
            bot=bot,
        )
