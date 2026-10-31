"""Application service for managing allowed commands per chat."""

from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.features.registry import COMMAND_PRESENTATION, FEATURE_REGISTRY
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import (
    PROVIDER_SPLIT_COMMANDS,
    CommandName,
)


def _provider_split(command_name: str) -> tuple[CommandName, CommandName] | None:
    """Resolve only registered provider parents to their capture commands."""
    try:
        command = CommandName(command_name)
    except ValueError:
        return None
    return PROVIDER_SPLIT_COMMANDS.get(command)


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
        presentation = COMMAND_PRESENTATION.get(command_name)
        if presentation is not None and not presentation.toggleable:
            return True

        allowed = set(await self._command_data_service.get_allowed_commands(chat_id))
        cmd_str = str(command_name)

        # Parent provider command is allowed if either video or post is enabled.
        split = _provider_split(cmd_str)
        if split is not None:
            video_cmd, post_cmd = split
            return str(video_cmd) in allowed or str(post_cmd) in allowed

        return cmd_str in allowed

    async def is_capture_allowed(self, chat_id: int, provider: CommandName, *, is_video: bool) -> bool:
        """Check whether video or post capture is enabled for a provider.

        Args:
            chat_id: The ID of the chat.
            provider: The provider CommandName.
            is_video: True for video items, False for image/post items.

        Returns:
            True if capture mode is allowed for the provider in the chat.

        """
        feature = FEATURE_REGISTRY.capture(provider, is_video=is_video)
        allowed = await self.get_allowed_commands(chat_id)
        if not allowed:
            return await self._command_data_service.legacy_capture_allowed(chat_id)

        return await self.is_command_allowed(chat_id, feature.key)

    async def enable_defaults(self, chat_id: int) -> None:
        """Enable all default media split commands for an approved chat.

        Args:
            chat_id: The ID of the chat.

        """
        defaults = tuple(feature.key for feature in FEATURE_REGISTRY.all() if feature.default_enabled)
        await self._command_data_service.initialize_defaults(chat_id, defaults)

    async def enable_command(
        self, chat_id: int, command_name: CommandName | str, *, actor: ActorContext | None = None
    ) -> bool:
        """Enable specific command for a chat.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to enable.
            actor: Authenticated actor for the transactional audit event.

        Returns:
            True if operation succeeded.

        """
        cmd_str = str(command_name)
        split = _provider_split(cmd_str)
        if split is not None:
            video_cmd, post_cmd = split
            res_video = await self._command_data_service.enable_command(chat_id, str(video_cmd), actor=actor)
            res_post = await self._command_data_service.enable_command(chat_id, str(post_cmd), actor=actor)
            return res_video and res_post

        return await self._command_data_service.enable_command(chat_id, cmd_str, actor=actor)

    async def disable_command(
        self, chat_id: int, command_name: CommandName | str, *, actor: ActorContext | None = None
    ) -> bool:
        """Disable specific command for a chat.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to disable.
            actor: Authenticated actor for the transactional audit event.

        Returns:
            True if operation succeeded, False if command is protected.

        """
        presentation = COMMAND_PRESENTATION.get(command_name)
        if presentation is not None and not presentation.toggleable:
            return False

        cmd_str = str(command_name)
        split = _provider_split(cmd_str)
        if split is not None:
            video_cmd, post_cmd = split
            res_video = await self._command_data_service.disable_command(chat_id, str(video_cmd), actor=actor)
            res_post = await self._command_data_service.disable_command(chat_id, str(post_cmd), actor=actor)
            return res_video or res_post

        return await self._command_data_service.disable_command(chat_id, cmd_str, actor=actor)

    async def toggle(self, chat_id: int, command_name: CommandName | str, *, actor: ActorContext | None = None) -> bool:
        """Toggle command between enabled and disabled, returning new state.

        Args:
            chat_id: The ID of the chat.
            command_name: The name of the command to toggle.
            actor: Authenticated actor for the transactional audit event.

        Returns:
            True if command is now enabled, False if disabled.

        """
        is_allowed = await self.is_command_allowed(chat_id, command_name)
        if is_allowed:
            await self.disable_command(chat_id, command_name, actor=actor)
            return False
        await self.enable_command(chat_id, command_name, actor=actor)
        return True

    async def get_allowed_commands(self, chat_id: int) -> set[str]:
        """Fetch all enabled commands for a chat (including active parent providers).

        Args:
            chat_id: The ID of the chat.

        Returns:
            Set of allowed command name strings.

        """
        allowed = set(await self._command_data_service.get_allowed_commands(chat_id))
        for provider, (video_cmd, post_cmd) in PROVIDER_SPLIT_COMMANDS.items():
            if str(video_cmd) in allowed or str(post_cmd) in allowed:
                allowed.add(str(provider))
        return allowed
