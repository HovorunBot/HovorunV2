"""Debug command module."""

from typing import Any

from aiogram import Bot
from aiogram.types import Message

from hovorunv2.application.data.constants import CommandName
from hovorunv2.application.services.access_service import CommandPolicy
from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand

logger = get_logger(__name__)


class DebugCommand(BaseCommand):
    """Command for debugging purposes."""

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.DEBUG

    @property
    def policy(self) -> CommandPolicy:
        """Admin only, bypasses whitelist."""
        return CommandPolicy(requires_admin=True, requires_whitelist=False, is_toggleable=False)

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /debug or /force_error."""
        if not message.text:
            return False
        return message.text.strip().startswith(("/debug", "/force_error"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ARG002
        """Handle debug commands."""
        if message.text and message.text.startswith("/force_error"):
            logger.info("Forcing error for testing purposes")
            msg = "Test error for Telegram DM logging"
            logger.error("Forced error triggered: %s", msg)
            raise ValueError(msg)

        logger.info("Executing debug command for user %s", message.from_user.id if message.from_user else "unknown")
        await message.answer(f"It works. Your message: {message.text}.")
