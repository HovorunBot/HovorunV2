"""Application service for post-deployment update notifications."""

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from hovorunv2.application.changelog import get_changelog_updates, get_current_version, parse_version
from hovorunv2.application.ports import ChatStore, SystemStore
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class NotificationService:
    """Service to handle broadcasting update notifications to group chats."""

    def __init__(self, chat_service: ChatStore, system_service: SystemStore) -> None:
        """Initialize with its dependencies."""
        self._chat_service = chat_service
        self._system_service = system_service

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

        tag_name = f"v{current_version.lstrip('v')}"
        tag_url = f"https://github.com/HovorunBot/HovorunV2/releases/tag/{tag_name}"
        message = (
            f"🚀 <b>Hovorun Updated to {tag_name}</b>\n\n"
            f"{updates_text}\n\n"
            f'📖 <a href="{tag_url}">Read full release notes on GitHub</a>'
        )

        logger.info("Broadcasting update notification to %d group chat(s)...", len(group_chats))

        for chat in group_chats:
            try:
                await self._send_notification(bot, chat.chat_id, message)
                # Respect rate limits (Telegram approx 30 msg/sec overall)
                await asyncio.sleep(0.05)
            except TelegramRetryAfter as e:
                logger.warning("Rate limit hit, sleeping for %d seconds", e.retry_after)
                await asyncio.sleep(e.retry_after)
                # Retry once
                try:
                    await self._send_notification(bot, chat.chat_id, message)
                except Exception:
                    logger.exception("Failed to retry update notification to group chat %d", chat.chat_id)
            except Exception:
                logger.exception("Failed to send update notification to group chat %d", chat.chat_id)

        await self._system_service.set_last_notified_version(current_version)
        logger.info("Update notification broadcast complete.")

    async def _send_notification(self, bot: Bot, chat_id: int, text: str) -> None:
        """Send formatted HTML update message with link preview disabled."""
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
