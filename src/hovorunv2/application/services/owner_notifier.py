"""Service for dispatching notifications to all registered bot owners."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

from hovorunv2.application.ports import AppSettings

logger = logging.getLogger(__name__)


class OwnerNotifier:
    """Dispatches direct messages to all registered bot owners."""

    def __init__(self, settings: AppSettings) -> None:
        """Initialize with application settings."""
        self._settings = settings

    async def broadcast(
        self,
        bot: Bot,
        text: str,
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None = None,
        *,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> int:
        """Broadcast text message to all registered owners, returning successful send count."""
        sent_count = 0
        for owner_id in self._settings.owners:
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
