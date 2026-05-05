"""Application service for managing allowed commands per chat."""

from hovorunv2.application.data.command_service import CommandDataService


class CommandService:
    """Service to handle command availability business rules.

    This service manages which commands are enabled for specific chats,
    ensuring default commands are always available and cannot be disabled.

    Attributes:
        DEFAULT_COMMANDS (tuple): Commands that are always available.

    """

    DEFAULT_COMMANDS = ("debug", "allow_chat", "enable_cmd", "disable_cmd", "set_lang")

    def __init__(self, command_data_service: CommandDataService) -> None:
        """Initialize the service.

        Args:
            command_data_service: Data service for command persistence.

        """
        self._command_data_service = command_data_service

    async def is_command_allowed(self, chat_id: int, command_name: str, platform: str = "telegram") -> bool:
        """Check if specific command is allowed in chat.

        Args:
            chat_id: The ID of the chat to check.
            command_name: The name of the command.
            platform: Messaging platform identifier.

        Returns:
            True if allowed, False otherwise.

        """
        if command_name in self.DEFAULT_COMMANDS:
            return True

        allowed = await self._command_data_service.get_allowed_commands(chat_id, platform)
        return command_name in allowed

    async def enable_command(self, chat_id: int, command_name: str, platform: str = "telegram") -> bool:
        """Enable specific command for a chat.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to enable.
            platform: Messaging platform identifier.

        Returns:
            True if operation succeeded.

        """
        return await self._command_data_service.enable_command(chat_id, platform, command_name)

    async def disable_command(self, chat_id: int, command_name: str, platform: str = "telegram") -> bool:
        """Disable specific command for a chat.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to disable.
            platform: Messaging platform identifier.

        Returns:
            True if operation succeeded, False if command is protected.

        """
        # Never disable default commands
        if command_name in self.DEFAULT_COMMANDS:
            return False

        return await self._command_data_service.disable_command(chat_id, platform, command_name)
