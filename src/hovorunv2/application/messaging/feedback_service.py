"""Application service for dispatching user feedback to bot administrators."""

import html
import logging

from aiogram import Bot

from hovorunv2.application.messaging.owner_notifier import OwnerNotifier

logger = logging.getLogger(__name__)


class FeedbackService:
    """Dispatch user feedback to all registered bot owners."""

    def __init__(self, owner_notifier: OwnerNotifier) -> None:
        """Initialize feedback service with owner notifier."""
        self._owner_notifier = owner_notifier

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
        """Dispatch user feedback to all registered bot owners in a private message.

        Args:
            bot: Telegram bot instance used for delivery.
            user_id: Telegram ID of the user who submitted the feedback.
            user_name: Display name of the submitting user.
            username: Optional username of the submitting user.
            chat_id: Telegram ID of the chat where the feedback originated.
            chat_title: Optional title of the source chat.
            chat_type: Telegram type of the source chat.
            message_text: Feedback text to deliver.

        Returns:
            True when delivered to at least one owner, False otherwise.
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

        sent_count = await self._owner_notifier.broadcast(bot, admin_payload)
        logger.info("Feedback from user %d delivered to %d owner(s)", user_id, sent_count)
        return sent_count > 0
