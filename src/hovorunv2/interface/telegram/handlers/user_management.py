"""Telegram handlers for user upgrade and tier management."""

from typing import Any, Final

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.data.constants import CommandName, UserTier
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.base import BaseCommand

logger = get_logger(__name__)

EXPECTED_SET_PARTS: Final[int] = 3
EXPECTED_INFO_PARTS: Final[int] = 2


class UserManagementCommand(BaseCommand):
    """Command to manage user tiers and beta access in private DM."""

    def __init__(self, access_service: AccessService) -> None:
        """Initialize command with dependencies."""
        self._access_service = access_service

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.SET_TIER

    @property
    def policy(self) -> CommandPolicy:
        """Requires administrator privileges in private DM."""
        return CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message triggers user management."""
        if not message.text:
            return False
        cmd = message.text.split()[0].lower().split("@")[0]
        return cmd in (f"/{CommandName.SET_TIER}", f"/{CommandName.USER_INFO}")

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Handle user management commands."""
        # Strictly reject in non-private chats
        if message.chat.type != ChatType.PRIVATE:
            return

        user_id = message.from_user.id if message.from_user else 0
        if not await self._access_service.is_admin(user_id):
            await message.answer("🚫 Only administrators can manage user tiers.")
            return

        if not message.text:
            return

        parts = message.text.strip().split()
        cmd = parts[0].lower().split("@")[0].removeprefix("/")

        match cmd:
            case CommandName.SET_TIER:
                await self._handle_set_tier(message, parts)
            case CommandName.USER_INFO:
                await self._handle_user_info(message, parts)
            case _:
                return

    async def _handle_set_tier(self, message: Message, parts: list[str]) -> None:
        """Process /set_tier <user_id> <tier>."""
        if len(parts) != EXPECTED_SET_PARTS:
            valid_tiers = "|".join(t.name.lower() for t in UserTier)
            usage = f"Usage: <code>/set_tier &lt;user_id&gt; &lt;{valid_tiers}&gt;</code>"
            await message.answer(usage, parse_mode="HTML")
            return

        target_id = self._parse_user_id(parts[1])
        if target_id is None:
            await message.answer("❌ Invalid user ID. Must be an integer.")
            return

        tier = self._parse_tier(parts[2])
        if tier is None:
            options = ", ".join(f"{t.name.lower()} ({int(t)})" for t in UserTier)
            await message.answer(f"❌ Invalid tier. Valid options: {options}")
            return

        await self._access_service.set_user_tier(target_id, tier)
        await message.answer(
            f"✅ User <code>{target_id}</code> tier set to <b>{tier.name}</b> ({int(tier)}).",
            parse_mode="HTML",
        )
        admin_id = message.from_user.id if message.from_user else 0
        logger.info("User %d tier changed to %s by admin %d", target_id, tier.name, admin_id)

    def _parse_user_id(self, raw_id: str) -> int | None:
        """Parse raw string to valid user ID integer."""
        try:
            return int(raw_id)
        except ValueError:
            return None

    def _parse_tier(self, raw_tier: str) -> UserTier | None:
        """Parse raw string into UserTier enum."""
        tier_input = raw_tier.upper()
        try:
            return UserTier(int(tier_input)) if tier_input.isdigit() else UserTier[tier_input]
        except (ValueError, KeyError):
            return None

    async def _handle_user_info(self, message: Message, parts: list[str]) -> None:
        """Process /user_info <user_id>."""
        if len(parts) != EXPECTED_INFO_PARTS:
            await message.answer("Usage: <code>/user_info &lt;user_id&gt;</code>", parse_mode="HTML")
            return

        target_id = self._parse_user_id(parts[1])
        if target_id is None:
            await message.answer("❌ Invalid user ID. Must be an integer.")
            return

        tier = await self._access_service.get_user_tier(target_id)
        has_beta = await self._access_service.has_beta_access(target_id)
        info_text = self._format_user_info(target_id, tier, has_beta=has_beta)
        await message.answer(info_text, parse_mode="HTML")

    def _format_user_info(self, target_id: int, tier: UserTier, *, has_beta: bool) -> str:
        """Construct user profile HTML text."""
        return (
            f"👤 <b>User Info:</b> <code>{target_id}</code>\n"
            f"<b>Tier:</b> {tier.name} (level {int(tier)})\n"
            f"<b>Beta Access:</b> {'✅ Yes' if has_beta else '❌ No'}\n"
            f"<b>Is Banned:</b> {'🚫 Yes' if tier <= UserTier.BANNED else 'No'}\n"
            f"<b>Is VIP:</b> {'⭐ Yes' if tier >= UserTier.VIP else 'No'}\n"
            f"<b>Is Admin:</b> {'🛡️ Yes' if tier >= UserTier.ADMIN else 'No'}"
        )

    def register(self, router: Router, flags: dict[str, Any]) -> None:
        """Register command handlers with router."""
        router.message.register(
            self.handle,
            Command(CommandName.SET_TIER, CommandName.USER_INFO),
            flags=flags,
        )
