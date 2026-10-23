"""Application service for managing allowed commands per chat."""

from hovorunv2.application.ports import CommandStore
from hovorunv2.domain.enums import (
    DEFAULT_COMMANDS,
    PROVIDER_SPLIT_COMMANDS,
    SPLIT_TO_PROVIDER_COMMAND,
    CommandName,
)


class CommandService:
    """Service to handle command availability business rules."""

    def __init__(self, command_data_service: CommandStore) -> None:
        """Initialize the service.

        Args:
            command_data_service: Data service for command persistence.

        """
        self._command_data_service = command_data_service

    async def is_command_allowed(self, chat_id: int, command_name: CommandName | str) -> bool:
        """Check if specific command is allowed in chat.

        Args:
            chat_id: The ID of the chat to check.
            command_name: The name of the command.

        Returns:
            True if allowed, False otherwise.

        """
        if command_name in DEFAULT_COMMANDS:
            return True

        allowed = set(await self._command_data_service.get_allowed_commands(chat_id))
        cmd_str = str(command_name)

        try:
            cmd_enum = CommandName(cmd_str)
        except ValueError:
            return cmd_str in allowed

        # Parent provider command is allowed if either video or post (or legacy parent) is enabled.
        if cmd_enum in PROVIDER_SPLIT_COMMANDS:
            video_cmd, post_cmd = PROVIDER_SPLIT_COMMANDS[cmd_enum]
            return str(video_cmd) in allowed or str(post_cmd) in allowed or cmd_str in allowed

        # Split sub-command (e.g. twitter_video or twitter_post)
        if cmd_enum in SPLIT_TO_PROVIDER_COMMAND:
            if cmd_str in allowed:
                return True
            parent_cmd = SPLIT_TO_PROVIDER_COMMAND[cmd_enum]
            video_cmd, post_cmd = PROVIDER_SPLIT_COMMANDS[parent_cmd]
            # Legacy fallback: if parent is enabled and neither split command is explicitly set
            return str(parent_cmd) in allowed and str(video_cmd) not in allowed and str(post_cmd) not in allowed

        return cmd_str in allowed

    async def enable_command(self, chat_id: int, command_name: CommandName | str) -> bool:
        """Enable specific command for a chat.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to enable.

        Returns:
            True if operation succeeded.

        """
        cmd_str = str(command_name)
        try:
            cmd_enum = CommandName(cmd_str)
        except ValueError:
            return await self._command_data_service.enable_command(chat_id, cmd_str)

        if cmd_enum in PROVIDER_SPLIT_COMMANDS:
            video_cmd, post_cmd = PROVIDER_SPLIT_COMMANDS[cmd_enum]
            res_parent = await self._command_data_service.enable_command(chat_id, cmd_str)
            res_video = await self._command_data_service.enable_command(chat_id, str(video_cmd))
            res_post = await self._command_data_service.enable_command(chat_id, str(post_cmd))
            return res_parent and res_video and res_post

        if cmd_enum in SPLIT_TO_PROVIDER_COMMAND:
            parent_cmd = SPLIT_TO_PROVIDER_COMMAND[cmd_enum]
            await self._command_data_service.enable_command(chat_id, str(parent_cmd))
            return await self._command_data_service.enable_command(chat_id, cmd_str)

        return await self._command_data_service.enable_command(chat_id, cmd_str)

    async def disable_command(self, chat_id: int, command_name: CommandName | str) -> bool:
        """Disable specific command for a chat.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to disable.

        Returns:
            True if operation succeeded, False if command is protected.

        """
        if command_name in DEFAULT_COMMANDS:
            return False

        cmd_str = str(command_name)
        try:
            cmd_enum = CommandName(cmd_str)
        except ValueError:
            return await self._command_data_service.disable_command(chat_id, cmd_str)

        if cmd_enum in PROVIDER_SPLIT_COMMANDS:
            video_cmd, post_cmd = PROVIDER_SPLIT_COMMANDS[cmd_enum]
            res_parent = await self._command_data_service.disable_command(chat_id, cmd_str)
            res_video = await self._command_data_service.disable_command(chat_id, str(video_cmd))
            res_post = await self._command_data_service.disable_command(chat_id, str(post_cmd))
            return res_parent or res_video or res_post

        if cmd_enum in SPLIT_TO_PROVIDER_COMMAND:
            parent_cmd = SPLIT_TO_PROVIDER_COMMAND[cmd_enum]
            video_cmd, post_cmd = PROVIDER_SPLIT_COMMANDS[parent_cmd]
            sibling_cmd = post_cmd if cmd_enum == video_cmd else video_cmd

            allowed = set(await self._command_data_service.get_allowed_commands(chat_id))
            # If legacy parent was enabled without explicit split commands, preserve sibling
            if str(parent_cmd) in allowed and str(video_cmd) not in allowed and str(post_cmd) not in allowed:
                await self._command_data_service.enable_command(chat_id, str(sibling_cmd))

            res = await self._command_data_service.disable_command(chat_id, cmd_str)
            updated_allowed = set(await self._command_data_service.get_allowed_commands(chat_id))
            if str(sibling_cmd) not in updated_allowed:
                await self._command_data_service.disable_command(chat_id, str(parent_cmd))
            return res

        return await self._command_data_service.disable_command(chat_id, cmd_str)

    async def get_allowed_commands(self, chat_id: int) -> set[str]:
        """Fetch all enabled commands for a chat."""
        allowed_list = await self._command_data_service.get_allowed_commands(chat_id)
        return set(allowed_list)
