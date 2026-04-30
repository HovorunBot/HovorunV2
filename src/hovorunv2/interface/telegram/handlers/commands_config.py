"""Handlers for managing chat command configuration."""

from typing import Any

from aiogram import Bot
from aiogram.types import Message

from hovorunv2.application.services.command_service import CommandService
from hovorunv2.infrastructure.config import Settings

from .base import BaseCommand


class EnableCommand(BaseCommand):
    """Handler for /enable_cmd command."""

    MIN_PARTS: int = 2
    COMMAND_NAME_INDEX: int = 1

    def __init__(self, command_service: CommandService, settings: Settings) -> None:
        """Initialize command with its dependencies."""
        self._command_service = command_service
        self._settings = settings

    @property
    def name(self) -> str:
        """Command name."""
        return "enable_cmd"

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /enable_cmd."""
        return bool(message.text and message.text.startswith("/enable_cmd"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Enable a command for the chat."""
        user_id = message.from_user.id if message.from_user else None
        if user_id not in self._settings.admin_ids:
            return

        parts = message.text.split() if message.text else []
        if len(parts) < self.MIN_PARTS:
            await message.reply("Usage: /enable_cmd <command_name>")
            return

        cmd_name = parts[self.COMMAND_NAME_INDEX].lower().lstrip("/")
        success = await self._command_service.enable_command(message.chat.id, cmd_name)
        if success:
            await message.reply(f"Command /{cmd_name} enabled for this chat.")
        else:
            await message.reply(f"Command /{cmd_name} not found or invalid.")


class DisableCommand(BaseCommand):
    """Handler for /disable_cmd command."""

    MIN_PARTS: int = 2
    COMMAND_NAME_INDEX: int = 1

    def __init__(self, command_service: CommandService, settings: Settings) -> None:
        """Initialize command with its dependencies."""
        self._command_service = command_service
        self._settings = settings

    @property
    def name(self) -> str:
        """Command name."""
        return "disable_cmd"

    async def is_triggered(self, message: Message) -> bool:
        """Check if message starts with /disable_cmd."""
        return bool(message.text and message.text.startswith("/disable_cmd"))

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Disable a command for the chat."""
        user_id = message.from_user.id if message.from_user else None
        if user_id not in self._settings.admin_ids:
            return

        parts = message.text.split() if message.text else []
        if len(parts) < self.MIN_PARTS:
            await message.reply("Usage: /disable_cmd <command_name>")
            return

        cmd_name = parts[self.COMMAND_NAME_INDEX].lower().lstrip("/")

        if cmd_name in self._command_service.DEFAULT_COMMANDS:
            await message.reply(f"Cannot disable default command /{cmd_name}.")
            return

        success = await self._command_service.disable_command(message.chat.id, cmd_name)
        if success:
            await message.reply(f"Command /{cmd_name} disabled for this chat.")
        else:
            await message.reply(f"Command /{cmd_name} not found or not enabled.")

