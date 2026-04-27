"""Handlers for managing chat command configuration."""

from typing import TYPE_CHECKING

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.container import container
from hovorunv2.interface.telegram.handlers.base import BaseCommand, register_command

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message


@register_command
class EnableCommand(BaseCommand):
    """Handler for /enable_cmd command."""

    MIN_PARTS: int = 2
    COMMAND_NAME_INDEX: int = 1

    @property
    def name(self) -> str:
        """Command name."""
        return "enable_cmd"

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /enable_cmd."""
        return bool(message.text and message.text.startswith("/enable_cmd"))

    async def handle(self, message: Message, bot: Bot) -> None:  # noqa: ARG002
        """Enable a command for the chat."""
        user_id = message.from_user.id if message.from_user else None
        if user_id not in settings.admin_ids:
            return

        if not container.command_service:
            return

        parts = message.text.split() if message.text else []
        if len(parts) < self.MIN_PARTS:
            await message.reply("Usage: /enable_cmd <command_name>")
            return

        cmd_name = parts[self.COMMAND_NAME_INDEX].lower().lstrip("/")
        success = await container.command_service.enable_command(message.chat.id, cmd_name)
        if success:
            await message.reply(f"Command /{cmd_name} enabled for this chat.")
        else:
            await message.reply(f"Command /{cmd_name} not found or invalid.")


@register_command
class DisableCommand(BaseCommand):
    """Handler for /disable_cmd command."""

    MIN_PARTS: int = 2
    COMMAND_NAME_INDEX: int = 1

    @property
    def name(self) -> str:
        """Command name."""
        return "disable_cmd"

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /disable_cmd."""
        return bool(message.text and message.text.startswith("/disable_cmd"))

    async def handle(self, message: Message, bot: Bot) -> None:  # noqa: ARG002
        """Disable a command for the chat."""
        user_id = message.from_user.id if message.from_user else None
        if user_id not in settings.admin_ids:
            return

        if not container.command_service:
            return

        parts = message.text.split() if message.text else []
        if len(parts) < self.MIN_PARTS:
            await message.reply("Usage: /disable_cmd <command_name>")
            return

        cmd_name = parts[self.COMMAND_NAME_INDEX].lower().lstrip("/")

        if cmd_name in container.command_service.DEFAULT_COMMANDS:
            await message.reply(f"Cannot disable default command /{cmd_name}.")
            return

        success = await container.command_service.disable_command(message.chat.id, cmd_name)
        if success:
            await message.reply(f"Command /{cmd_name} disabled for this chat.")
        else:
            await message.reply(f"Command /{cmd_name} not found or not enabled.")
