"""Application service for managing chat access status."""

from hovorunv2.application.data.chat_service import ChatService
from hovorunv2.application.data.constants import ChatStatus


class ChatStatusService:
    """Service to handle chat lifecycle and access status.

    This service manages whether a chat is allowed to use the bot's
    extended functionality (APPROVED) via ChatStatus state machine.
    """

    def __init__(self, chat_service: ChatService) -> None:
        """Initialize the service.

        Args:
            chat_service: Data service for chat persistence.

        """
        self._chat_service = chat_service

    async def get_status(self, chat_id: int, platform: str = "telegram") -> ChatStatus:
        """Get the current lifecycle status of a chat."""
        chat = await self._chat_service.get_chat(chat_id, platform)
        if not chat:
            return ChatStatus.UNAUTHORIZED
        return ChatStatus(chat.status)

    async def set_status(self, chat_id: int, status: ChatStatus, platform: str = "telegram") -> None:
        """Update the lifecycle status of a chat."""
        await self._chat_service.update_status(chat_id, platform, status)

    async def is_approved(self, chat_id: int, platform: str = "telegram") -> bool:
        """Check if chat is approved (APPROVED).

        Args:
            chat_id: The ID of the chat.
            platform: Messaging platform identifier.

        Returns:
            True if status is APPROVED, False otherwise.

        """
        status = await self.get_status(chat_id, platform)
        return status == ChatStatus.APPROVED
