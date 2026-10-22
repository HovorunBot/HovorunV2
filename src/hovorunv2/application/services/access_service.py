"""Application service for unified access control and permissions."""

from typing import TYPE_CHECKING, Final

from hovorunv2.application.data.constants import UserTier
from hovorunv2.application.data.user_service import UserDataService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.infrastructure.cache import CacheService

if TYPE_CHECKING:
    from aiogram import Bot

USER_CACHE_TTL: Final[int] = 300


class CommandPolicy:
    """Strategy for validating command execution.

    Allows for declarative validation rules (admin, approval, toggleable, tier, beta)
    while supporting complex custom logic by overriding evaluate().
    """

    def __init__(
        self,
        *,
        requires_admin: bool = False,
        requires_group_admin: bool = False,
        requires_approval: bool = True,
        is_toggleable: bool = True,
        auto_enable: bool = False,
        min_tier: UserTier = UserTier.USER,
        requires_beta: bool = False,
    ) -> None:
        """Initialize policy with declarative rules."""
        self.requires_admin = requires_admin
        self.requires_group_admin = requires_group_admin
        self.requires_approval = requires_approval
        self.is_toggleable = is_toggleable
        self.auto_enable = auto_enable
        self.min_tier = min_tier
        self.requires_beta = requires_beta

    async def evaluate(
        self,
        user_id: int | None,
        chat_id: int,
        command_name: str | None,
        platform: str,
        access_service: AccessService,
        chat_status_service: ChatStatusService,
        command_service: CommandService,
        bot: Bot | None = None,
    ) -> bool:
        """Evaluate access rules.

        Admins/Owners bypass all checks unless specific policy says otherwise.
        """
        # 0. Banned users are denied completely
        if user_id and await access_service.is_banned(user_id, platform):
            return False

        # 1. Feature toggles apply to everyone, including admins.
        # If a command is explicitly disabled in this chat, nobody can use it.
        if (
            self.is_toggleable
            and command_name
            and not await command_service.is_command_allowed(chat_id, command_name, platform)
        ):
            return False

        # 2. Beta access check
        if self.requires_beta and (not user_id or not await access_service.has_beta_access(user_id, platform)):
            return False

        # 3. Global Admin always has full access
        if user_id and await access_service.is_admin(user_id, platform):
            return True

        # 4. Check if global admin or tier/group permissions are satisfied
        if self.requires_admin or not await self._meets_tier_and_group(
            user_id, chat_id, platform, access_service, bot
        ):
            return False

        # 5. Approval check
        return not (self.requires_approval and not await chat_status_service.is_approved(chat_id, platform))

    async def _meets_tier_and_group(
        self,
        user_id: int | None,
        chat_id: int,
        platform: str,
        access_service: AccessService,
        bot: Bot | None,
    ) -> bool:
        """Verify tier requirements and group admin permissions."""
        if self.min_tier > UserTier.USER and not user_id:
            return False
        if user_id and not await access_service.meets_tier(user_id, self.min_tier, platform):
            return False
        if self.requires_group_admin:
            if not user_id or not bot:
                return False
            return await access_service.is_group_admin(bot, chat_id, user_id, platform)
        return True


class AccessService:
    """Service to handle cross-platform access control logic.

    Centralizes rules for 'Who can do what' across different messaging platforms.
    Delegates specific validation logic to CommandPolicy objects.
    """

    def __init__(
        self,
        user_data_service: UserDataService,
        chat_status_service: ChatStatusService,
        command_service: CommandService,
        cache_service: CacheService,
    ) -> None:
        """Initialize service with dependencies."""
        self._user_data_service = user_data_service
        self._chat_status_service = chat_status_service
        self._command_service = command_service
        self._cache_service = cache_service

    def _resolve_tier(self, raw_tier: int) -> UserTier:
        """Resolve an integer tier to the appropriate UserTier enum member."""
        try:
            return UserTier(raw_tier)
        except ValueError:
            matching = [t for t in UserTier if int(t) <= raw_tier]
            return max(matching) if matching else UserTier.BANNED

    async def get_user_tier(self, user_id: int, platform: str = "telegram") -> UserTier:
        """Get user authorization tier with caching."""
        cache_key = f"user_tier:{platform}:{user_id}"
        cached_tier = await self._cache_service.get(cache_key)
        if cached_tier is not None:
            return self._resolve_tier(int(cached_tier))

        user = await self._user_data_service.get_user(user_id, platform)
        tier = self._resolve_tier(user.tier) if user else UserTier.USER
        await self._cache_service.set(cache_key, int(tier), expire=USER_CACHE_TTL)
        return tier

    async def has_beta_access(self, user_id: int, platform: str = "telegram") -> bool:
        """Check if user has access to beta features."""
        return await self.meets_tier(user_id, UserTier.BETA, platform)

    async def meets_tier(self, user_id: int, min_tier: UserTier, platform: str = "telegram") -> bool:
        """Check if user's tier meets or exceeds the required tier."""
        tier = await self.get_user_tier(user_id, platform)
        return tier >= min_tier

    async def is_banned(self, user_id: int, platform: str = "telegram") -> bool:
        """Check if a user is explicitly banned."""
        tier = await self.get_user_tier(user_id, platform)
        return tier <= UserTier.BANNED

    async def is_vip(self, user_id: int, platform: str = "telegram") -> bool:
        """Check if a user has VIP privileges (or higher)."""
        return await self.meets_tier(user_id, UserTier.VIP, platform)

    async def is_admin(self, user_id: int, platform: str = "telegram") -> bool:
        """Check if a user has administrative privileges on a specific platform."""
        return await self.meets_tier(user_id, UserTier.ADMIN, platform)

    async def get_admin_ids(self, platform: str = "telegram") -> list[int]:
        """Fetch all admin user IDs."""
        admins = await self._user_data_service.get_admins(platform)
        return [admin.user_id for admin in admins]

    async def set_user_tier(self, user_id: int, tier: UserTier, platform: str = "telegram") -> None:
        """Set user tier and invalidate cache."""
        await self._user_data_service.set_tier(user_id, tier, platform)
        await self._cache_service.delete(f"user_tier:{platform}:{user_id}")

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
        """Check if an action is allowed based on the provided policy."""
        return await policy.evaluate(
            user_id=user_id,
            chat_id=chat_id,
            command_name=command_name,
            platform=platform,
            access_service=self,
            chat_status_service=self._chat_status_service,
            command_service=self._command_service,
            bot=bot,
        )
