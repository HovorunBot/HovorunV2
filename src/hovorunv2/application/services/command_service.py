"""Application service for managing allowed commands per chat."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hovorunv2.application.data.command_service import CommandDataService


class CommandService:
    """Service to handle command availability business rules."""

    DEFAULT_COMMANDS = ("debug", "allow_chat", "enable_cmd", "disable_cmd", "set_lang")

    def __init__(self, command_data_service: CommandDataService) -> None:
        """Initialize with data service."""
        self._command_data_service = command_data_service

    async def is_command_allowed(self, chat_id: int, command_name: str, platform: str = "telegram") -> bool:
        """Check if specific command is allowed in chat."""
        if command_name in self.DEFAULT_COMMANDS:
            return True

        allowed = await self._command_data_service.get_allowed_commands(chat_id, platform)
        return command_name in allowed

    async def enable_command(self, chat_id: int, command_name: str, platform: str = "telegram") -> bool:
        """Enable specific command for a chat."""
        return await self._command_data_service.enable_command(chat_id, platform, command_name)

    async def disable_command(self, chat_id: int, command_name: str, platform: str = "telegram") -> bool:
        """Disable specific command for a chat."""
        # Never disable default commands
        if command_name in self.DEFAULT_COMMANDS:
            return False

        return await self._command_data_service.disable_command(chat_id, platform, command_name)
