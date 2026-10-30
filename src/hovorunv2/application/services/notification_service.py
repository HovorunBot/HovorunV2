"""Application service for post-deployment update notifications."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from hovorunv2.application.changelog import get_changelog_updates, get_current_version, parse_version
from hovorunv2.application.ports import AppSettings, ChatStore, SystemStore
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus

logger = logging.getLogger(__name__)


class NotificationService:
    """Service to handle broadcasting update notifications to group chats."""

    def __init__(self, chat_service: ChatStore, system_service: SystemStore, settings: AppSettings) -> None:
        """Initialize with its dependencies."""
        self._chat_service = chat_service
        self._system_service = system_service
        self._settings = settings

    async def notify_updates(self, bot: Bot) -> None:
        """Check for version updates and notify approved group chats."""
        current_version = get_current_version()
        last_notified = await self._system_service.get_last_notified_version()

        # 1. Fresh database: record current version silently without notification
        if last_notified is None:
            logger.info("Fresh database detected. Initializing version %s silently", current_version)
            await self._system_service.set_last_notified_version(current_version)
            return

        # 2. Only notify if version has strictly increased
        if parse_version(current_version) <= parse_version(last_notified):
            logger.debug(
                "No version upgrade detected (last notified: %s, current: %s). Skipping notification.",
                last_notified,
                current_version,
            )
            return

        # 3. Retrieve user-facing changes between last_notified and current_version
        updates_text = get_changelog_updates(last_notified, current_version)
        if not updates_text:
            logger.info("No user-facing changelog entries found between %s and %s", last_notified, current_version)
            await self._system_service.set_last_notified_version(current_version)
            return

        # 4. Filter strictly to approved group/supergroup chats (chat_id < 0)
        approved_chats = await self._chat_service.get_all_by_status(ChatStatus.APPROVED, "telegram")
        group_chats = [chat for chat in approved_chats if chat.chat_id < 0]
        if not group_chats:
            logger.info("No approved group chats found to notify.")
            await self._system_service.set_last_notified_version(current_version)
            return

        message = self._release_message(current_version, updates_text)
        await self._broadcast_updates(bot, group_chats, message)
        await self._system_service.set_last_notified_version(current_version)
        logger.info("Update notification broadcast complete.")

    def _release_message(self, current_version: str, updates_text: str) -> str:
        """Render the selected release notes and link."""
        tag_name = f"v{current_version.lstrip('v')}"
        tag_url = f"{self._settings.github_url.rstrip('/')}/releases/tag/{tag_name}"
        return (
            f"🚀 <b>Hovorun Updated to {tag_name}</b>\n\n"
            f"{updates_text}\n\n"
            f'📖 <a href="{tag_url}">Read full release notes on GitHub</a>'
        )

    async def _broadcast_updates(self, bot: Bot, group_chats: list[ChatDB], message: str) -> None:
        """Send to each eligible group, preserving order and rate-limit recovery."""
        logger.info("Broadcasting update notification to %d group chat(s)...", len(group_chats))
        for chat in group_chats:
            await self._send_group_update(bot, chat.chat_id, message)

    async def _send_group_update(self, bot: Bot, chat_id: int, message: str) -> None:
        try:
            await self._send_notification(bot, chat_id, message)
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as error:
            logger.warning("Rate limit hit, sleeping for %d seconds", error.retry_after)
            await asyncio.sleep(error.retry_after)
            try:
                await self._send_notification(bot, chat_id, message)
            except Exception:
                logger.exception("Failed to retry update notification to group chat %d", chat_id)
        except Exception:
            logger.exception("Failed to send update notification to group chat %d", chat_id)

    async def _send_notification(self, bot: Bot, chat_id: int, text: str) -> None:
        """Send formatted HTML update message with link preview disabled."""
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
