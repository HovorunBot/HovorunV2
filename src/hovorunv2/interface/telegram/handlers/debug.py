"""Debug command module."""

from typing import Any, ClassVar

from aiogram import Bot
from aiogram.types import Message

from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand

logger = get_logger(__name__)


class DebugCommand(BaseCommand):
    """Command for debugging purposes."""

    BYPASS_WHITELIST: ClassVar[bool] = True

    @property
    def name(self) -> str:
        """Command name."""
        return "debug"

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /debug or /force_error."""
        if not message.text:
            return False
        return message.text.strip().startswith(("/debug", "/force_error"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Handle debug commands."""
        if message.text and message.text.startswith("/force_error"):
            logger.info("Forcing error for testing purposes")
            msg = "Test error for Telegram DM logging"
            logger.error("Forced error triggered: %s", msg)
            raise ValueError(msg)

        logger.info("Executing debug command for user %s", message.from_user.id if message.from_user else "unknown")
        await message.answer(f"It works. Your message: {message.text}.")
