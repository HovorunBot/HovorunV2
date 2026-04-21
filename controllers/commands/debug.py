"""Debug command module."""

from aiogram import Bot
from aiogram.types import Message

from logger_conf import get_logger

from .base import BaseCommand, register_command

logger = get_logger(__name__)


@register_command
class DebugCommand(BaseCommand):
    """Command for debugging purposes."""

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /debug."""
        return bool(message.text and message.text.startswith("/debug"))

    async def handle(self, message: Message, bot: Bot) -> None:  # noqa: ARG002
        """Handle debug command."""
        logger.info("Executing debug command for user %s", message.from_user.id if message.from_user else "unknown")
        await message.answer(f"It works. Your message: {message.text}.")
