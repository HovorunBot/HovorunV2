"""Debug command module."""

from typing import Any, ClassVar

from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand

logger = get_logger(__name__)


class DebugCommand(BaseCommand):
    """Command for debugging purposes."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.DEBUG

    @property
    def trigger(self) -> Command:
        """Trigger filter for debug command."""
        return Command(CommandName.DEBUG, "force_error")

    async def is_triggered(self, message: Message) -> bool:
        """Return whether the message text targets /debug or /force_error.

        Args:
            message: Telegram message to inspect.

        Returns:
            True if the message text starts with /debug or /force_error.
        """
        if not message.text:
            return False
        parts = message.text.strip().split()
        if not parts:
            return False
        cmd_part = parts[0].lstrip("/").split("@")[0].lower()
        return cmd_part in (CommandName.DEBUG, "force_error")

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Handle debug commands.

        Args:
            message: Telegram message triggering the command.
            bot: Telegram bot bound to the handler.
            **kwargs: Additional handler context, unused.

        Raises:
            ValueError: When /force_error is requested, to exercise error logging.
        """
        if message.text and message.text.startswith("/force_error"):
            logger.info("Forcing error for testing purposes")
            msg = "Test error for Telegram DM logging"
            logger.error("Forced error triggered: %s", msg)
            raise ValueError(msg)

        logger.info("Executing debug command for user %s", message.from_user.id if message.from_user else "unknown")
        await message.answer(f"It works. Your message: {message.text}.")
