"""Service for dispatching notifications to all registered bot owners."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

from hovorunv2.application.access.role_service import RoleService
from hovorunv2.domain.enums import Role

logger = logging.getLogger(__name__)


class OwnerNotifier:
    """Dispatches direct messages to all registered bot owners."""

    def __init__(self, roles: RoleService) -> None:
        """Initialize with durable role lookup."""
        self._roles = roles

    async def broadcast(
        self,
        bot: Bot,
        text: str,
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None = None,
        *,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        include_admins: bool = False,
    ) -> int:
        """Broadcast to active owners, optionally including bot admins for access-request review."""
        sent_count = 0
        for owner in await self._roles.list_roles(None if include_admins else Role.OWNER):
            owner_id = owner.telegram_id
            try:
                await bot.send_message(
                    chat_id=owner_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                sent_count += 1
            except TelegramForbiddenError:
                logger.warning("Failed to notify owner %d: Bot is blocked by user", owner_id)
            except Exception:
                logger.exception("Failed to notify owner %d", owner_id)
        return sent_count
