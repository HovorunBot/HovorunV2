"""Whitelist command module."""

from collections.abc import Sequence
from typing import Any, ClassVar

from aiogram import Bot
from aiogram.types import Message

from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand

logger = get_logger(__name__)


class AllowBotCommand(BaseCommand):
    """Command to allow bot in a chat."""

    def __init__(
        self,
        whitelist_service: WhitelistService,
        command_service: CommandService,
        settings: Settings,
        commands: Sequence[BaseCommand],
    ) -> None:
        """Initialize command with its dependencies."""
        self._whitelist_service = whitelist_service
        self._command_service = command_service
        self._settings = settings
        self._commands = commands

    @property
    def name(self) -> str:
        """Command name."""
        return "allow_chat"

    BYPASS_WHITELIST: ClassVar[bool] = True

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /allow_chat."""
        return bool(message.text and message.text.strip() == "/allow_chat")

    async def handle(
        self,
        message: Message,
        bot: Bot,  # noqa: ARG002
        **kwargs: Any,  # noqa: ANN401, ARG002
    ) -> None:
        """Handle allow bot command."""
        user_id = message.from_user.id if message.from_user else None
        if user_id not in self._settings.admin_ids:
            logger.warning("Unauthorized /allow_bot attempt by user %s", user_id)
            return

        chat_id = message.chat.id
        await self._whitelist_service.add_to_whitelist(chat_id)

        # Auto-allow commands
        for command in (c for c in self._commands if c.AUTO_ALLOW):
            await self._command_service.enable_command(chat_id, command.name)

        await message.answer("Bot is now allowed in this chat.")
        logger.info("Bot allowed in chat %d by user %d", chat_id, user_id)
