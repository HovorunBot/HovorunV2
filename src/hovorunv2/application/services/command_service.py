"""Application service for managing allowed commands per chat."""

from hovorunv2.application.data.command_service import CommandDataService
from hovorunv2.application.data.constants import DEFAULT_COMMANDS, CommandName


class CommandService:
    """Service to handle command availability business rules."""

    def __init__(self, command_data_service: CommandDataService) -> None:
        """Initialize the service.

        Args:
            command_data_service: Data service for command persistence.

        """
        self._command_data_service = command_data_service

    async def is_command_allowed(
        self, chat_id: int, command_name: CommandName | str, platform: str = "telegram"
    ) -> bool:
        """Check if specific command is allowed in chat.

        Args:
            chat_id: The ID of the chat to check.
            command_name: The name of the command.
            platform: Messaging platform identifier.

        Returns:
            True if allowed, False otherwise.

        """
        if command_name in DEFAULT_COMMANDS:
            return True

        allowed = await self._command_data_service.get_allowed_commands(chat_id, platform)
        return command_name in allowed

    async def enable_command(self, chat_id: int, command_name: CommandName | str, platform: str = "telegram") -> bool:
        """Enable specific command for a chat.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to enable.
            platform: Messaging platform identifier.

        Returns:
            True if operation succeeded.

        """
        return await self._command_data_service.enable_command(chat_id, platform, str(command_name))

    async def disable_command(self, chat_id: int, command_name: CommandName | str, platform: str = "telegram") -> bool:
        """Disable specific command for a chat.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to disable.
            platform: Messaging platform identifier.

        Returns:
            True if operation succeeded, False if command is protected.

        """
        # Never disable default commands
        if command_name in DEFAULT_COMMANDS:
            return False

        return await self._command_data_service.disable_command(chat_id, platform, str(command_name))

    async def get_allowed_commands(self, chat_id: int, platform: str = "telegram") -> set[str]:
        """Fetch all enabled commands for a chat."""
        allowed_list = await self._command_data_service.get_allowed_commands(chat_id, platform)
        return set(allowed_list)
