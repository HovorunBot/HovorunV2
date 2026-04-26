"""Whitelist command module."""

from typing import TYPE_CHECKING, ClassVar

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

from .base import BaseCommand, get_commands, register_command

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

logger = get_logger(__name__)


@register_command
class AllowBotCommand(BaseCommand):
    """Command to allow bot in a chat."""

    @property
    def name(self) -> str:
        """Command name."""
        return "allow_chat"

    BYPASS_WHITELIST: ClassVar[bool] = True

    async def is_triggered(self, message: Message) -> bool:
        """Check if message is /allow_chat."""
        return bool(message.text and message.text.strip() == "/allow_chat")

    async def handle(self, message: Message, bot: Bot) -> None:  # noqa: ARG002
        """Handle allow bot command."""
        if not container.whitelist_service:
            logger.error("WhitelistService not available in container")
            return

        user_id = message.from_user.id if message.from_user else None
        if user_id not in settings.admin_ids:
            logger.warning("Unauthorized /allow_bot attempt by user %s", user_id)
            return

        chat_id = message.chat.id
        await container.whitelist_service.add_to_whitelist(chat_id)

        # Auto-allow commands
        for command in (c for c in get_commands().values() if c.AUTO_ALLOW):
            await container.command_service.enable_command(chat_id, command.name)

        await message.answer("Bot is now allowed in this chat.")
        logger.info("Bot allowed in chat %d by user %d", chat_id, user_id)
