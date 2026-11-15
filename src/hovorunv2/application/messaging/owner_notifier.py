"""Service for dispatching notifications to all registered bot owners."""

import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

from hovorunv2.application.access.role_service import RoleService
from hovorunv2.domain.enums import Role

logger = logging.getLogger(__name__)

type ReplyMarkup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None


class OwnerNotifier:
    """Dispatches direct messages to all registered bot owners."""

    def __init__(self, roles: RoleService) -> None:
        """Initialize with durable role lookup."""
        self._roles = roles

    async def _send_direct(
        self,
        bot: Bot,
        owner_id: int,
        text: str,
        reply_markup: ReplyMarkup,
        *,
        parse_mode: str,
        disable_web_page_preview: bool,
    ) -> bool:
        try:
            await bot.send_message(
                chat_id=owner_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        except TelegramForbiddenError:
            logger.warning("Failed to notify owner %d: Bot is blocked by user", owner_id)
            return False
        except TelegramAPIError:
            logger.exception("Failed to notify owner %d", owner_id)
            return False
        else:
            return True

    async def broadcast(
        self,
        bot: Bot,
        text: str,
        reply_markup: ReplyMarkup = None,
        *,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        include_admins: bool = False,
    ) -> int:
        """Broadcast a message to active owners, optionally including bot admins for access-request review.

        Args:
            bot: Telegram bot instance used to send messages.
            text: Message text to deliver.
            reply_markup: Optional inline or reply keyboard markup.
            parse_mode: Telegram parse mode for the text.
            disable_web_page_preview: Whether to disable link previews.
            include_admins: Whether to include admins alongside owners.

        Returns:
            Number of messages successfully sent.
        """
        sent_count = 0
        for owner in await self._roles.list_roles(None if include_admins else Role.OWNER):
            if await self._send_direct(
                bot,
                owner.telegram_id,
                text,
                reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            ):
                sent_count += 1
        return sent_count

    async def broadcast_localized(
        self,
        bot: Bot,
        render_message: Callable[[int], Awaitable[tuple[str, ReplyMarkup]]],
        *,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        include_admins: bool = False,
    ) -> int:
        """Broadcast a recipient-localized message to active owners and optional admins.

        Args:
            bot: Telegram bot instance used to send messages.
            render_message: Async callback returning ``(text, reply_markup)`` for a recipient ID.
            parse_mode: Telegram parse mode for the text.
            disable_web_page_preview: Whether to disable link previews.
            include_admins: Whether to include admins alongside owners.

        Returns:
            Number of messages successfully sent.
        """
        sent_count = 0
        for owner in await self._roles.list_roles(None if include_admins else Role.OWNER):
            text, reply_markup = await render_message(owner.telegram_id)
            if await self._send_direct(
                bot,
                owner.telegram_id,
                text,
                reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            ):
                sent_count += 1
        return sent_count
