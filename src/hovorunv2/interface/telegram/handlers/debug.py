"""Debug command module."""

from typing import TYPE_CHECKING, ClassVar

from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand, register_command

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

logger = get_logger(__name__)


@register_command
class DebugCommand(BaseCommand):
    """Command for debugging purposes."""

    BYPASS_WHITELIST: ClassVar[bool] = True

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /debug."""
        return bool(message.text and message.text.strip().startswith("/debug"))

    async def handle(self, message: Message, bot: Bot) -> None:  # noqa: ARG002
        """Handle debug command."""
        logger.info("Executing debug command for user %s", message.from_user.id if message.from_user else "unknown")
        await message.answer(f"It works. Your message: {message.text}.")
