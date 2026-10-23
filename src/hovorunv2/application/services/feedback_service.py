"""Application service for handling user feedback submission."""

import html
import logging
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramForbiddenError

from hovorunv2.application.ports import AppSettings

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


class FeedbackService:
    """Service to handle user feedback processing and delivery to owners."""

    def __init__(self, settings: AppSettings) -> None:
        """Initialize feedback service with application settings."""
        self._settings = settings

    async def submit_feedback(
        self,
        bot: Bot,
        *,
        user_id: int,
        user_name: str,
        username: str | None,
        chat_id: int,
        chat_title: str | None,
        chat_type: str,
        message_text: str,
    ) -> bool:
        """Dispatch user feedback to all registered bot owners in DM.

        Returns True if delivered to at least one owner, False otherwise.
        """
        escaped_user = html.escape(user_name)
        escaped_username = f"@{username}" if username else "N/A"
        escaped_chat_title = html.escape(chat_title or "Private")
        escaped_content = html.escape(message_text.strip())

        admin_payload = (
            "📬 <b>New User Feedback Received</b>\n\n"
            f"<b>From:</b> {escaped_user} (ID: <code>{user_id}</code>, {escaped_username})\n"
            f"<b>Chat:</b> {escaped_chat_title} (ID: <code>{chat_id}</code>, Type: <code>{chat_type}</code>)\n\n"
            f"<b>Message:</b>\n{escaped_content}"
        )

        sent_count = 0
        for owner_id in self._settings.owners:
            try:
                await bot.send_message(chat_id=owner_id, text=admin_payload, parse_mode="HTML")
                sent_count += 1
            except TelegramForbiddenError:
                logger.warning("Cannot deliver feedback to owner %d: bot is blocked or DM uninitiated", owner_id)
            except Exception:
                logger.exception("Failed to deliver feedback to owner %d", owner_id)

        logger.info("Feedback from user %d delivered to %d owner(s)", user_id, sent_count)
        return sent_count > 0
