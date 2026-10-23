"""Application service for managing chat access status."""

from hovorunv2.application.ports import ChatStore
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.enums import ChatStatus


class ChatStatusService:
    """Service to handle chat lifecycle and access status.

    This service manages whether a chat is allowed to use the bot's
    extended functionality (APPROVED) via ChatStatus state machine.
    """

    def __init__(self, chat_service: ChatStore, command_service: CommandService) -> None:
        """Initialize the service.

        Args:
            chat_service: Data service for chat persistence.
            command_service: Application service for command configuration.

        """
        self._chat_service = chat_service
        self._command_service = command_service

    async def get_status(self, chat_id: int) -> ChatStatus:
        """Get the current lifecycle status of a chat."""
        chat = await self._chat_service.get_chat(chat_id)
        if not chat:
            return ChatStatus.UNAUTHORIZED
        return ChatStatus(chat.status)

    async def set_status(self, chat_id: int, status: ChatStatus) -> None:
        """Update the lifecycle status of a chat."""
        await self._chat_service.update_status(chat_id, status)

    async def approve(self, chat_id: int) -> None:
        """Approve a chat and enable its default command set."""
        await self.set_status(chat_id, ChatStatus.APPROVED)
        await self._command_service.enable_defaults(chat_id)

    async def is_approved(self, chat_id: int) -> bool:
        """Check if chat is approved (APPROVED).

        Args:
            chat_id: The ID of the chat.

        Returns:
            True if status is APPROVED, False otherwise.

        """
        status = await self.get_status(chat_id)
        return status == ChatStatus.APPROVED
