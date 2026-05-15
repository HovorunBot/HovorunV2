"""Application service for post-deployment update notifications."""

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from hovorunv2.application.changelog import get_changelog_updates, get_current_version
from hovorunv2.application.data.chat_service import ChatService
from hovorunv2.application.data.constants import ChatStatus
from hovorunv2.application.data.system_service import SystemDataService
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class NotificationService:
    """Service to handle broadcasting update notifications to users."""

    def __init__(self, chat_service: ChatService, system_service: SystemDataService) -> None:
        """Initialize with its dependencies."""
        self._chat_service = chat_service
        self._system_service = system_service

    async def notify_updates(self, bot: Bot) -> None:
        """Check for version updates and notify approved chats."""
        current_version = get_current_version()
        last_notified = await self._system_service.get_last_notified_version()

        # If fresh install, treat as update from 0.0.0 to show initial changes
        if last_notified is None:
            logger.info("Fresh install detected. Checking for initial version %s changes", current_version)
            last_notified = "0.0.0"

        if last_notified == current_version:
            logger.debug("No new updates to notify. Current version: %s", current_version)
            return

        updates_text = get_changelog_updates(last_notified, current_version)
        if not updates_text:
            logger.info("No changelog entries found between %s and %s", last_notified, current_version)
            await self._system_service.set_last_notified_version(current_version)
            return

        message = f"🚀 *Hovorun Updated to v{current_version}*\n\n{updates_text}\n\n_Enjoy the new features!_"

        approved_chats = await self._chat_service.get_all_by_status(ChatStatus.APPROVED, "telegram")
        if not approved_chats:
            logger.info("No approved chats to notify.")
            await self._system_service.set_last_notified_version(current_version)
            return

        logger.info("Broadcasting update notification to %d chats...", len(approved_chats))

        for chat in approved_chats:
            try:
                await bot.send_message(
                    chat_id=chat.chat_id,
                    text=message,
                    parse_mode="Markdown",  # Simplified markdown for compatibility
                )
                # Respect rate limits (Telegram approx 30 msg/sec overall)
                await asyncio.sleep(0.05)
            except TelegramRetryAfter as e:
                logger.warning("Rate limit hit, sleeping for %d seconds", e.retry_after)
                await asyncio.sleep(e.retry_after)
                # Retry once
                await bot.send_message(chat_id=chat.chat_id, text=message, parse_mode="Markdown")
            except Exception:
                logger.exception("Failed to send update notification to chat %d", chat.chat_id)

        await self._system_service.set_last_notified_version(current_version)
        logger.info("Update notification broadcast complete.")
