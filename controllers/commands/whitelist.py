"""Whitelist command module."""

from aiogram import Bot
from aiogram.types import Message

from config import settings
from database import DatabaseService
from logger_conf import get_logger

from .base import BaseCommand, register_command

logger = get_logger(__name__)


@register_command
class AllowBotCommand(BaseCommand):
    """Command to allow bot in a chat."""

    def __init__(self) -> None:
        self.db = DatabaseService()

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /allow_bot."""
        return bool(message.text and message.text.strip() == "/allow_bot")

    async def handle(self, message: Message, bot: Bot) -> None:  # noqa: ARG002
        """Handle allow bot command."""
        user_id = message.from_user.id if message.from_user else None
        if user_id not in settings.admin_ids:
            logger.warning("Unauthorized /allow_bot attempt by user %s", user_id)
            return

        chat_id = message.chat.id
        self.db.add_to_whitelist(chat_id)
        await message.answer("Bot is now allowed in this chat.")
        logger.info("Bot allowed in chat %d by user %d", chat_id, user_id)
