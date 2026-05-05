"""Application service for managing chat whitelisting."""

from hovorunv2.application.data.chat_service import ChatService


class WhitelistService:
    """Service to handle whitelisting business rules.

    This service manages whether a chat is allowed to use the bot's
    extended functionality (whitelisted).
    """

    def __init__(self, chat_service: ChatService) -> None:
        """Initialize the service.

        Args:
            chat_service: Data service for chat persistence.

        """
        self._chat_service = chat_service

    async def is_whitelisted(self, chat_id: int, platform: str = "telegram") -> bool:
        """Check if chat is whitelisted.

        Args:
            chat_id: The ID of the chat.
            platform: Messaging platform identifier.

        Returns:
            True if whitelisted, False otherwise.

        """
        chat = await self._chat_service.get_chat(chat_id, platform)
        return chat.is_whitelisted if chat else False

    async def add_to_whitelist(self, chat_id: int, platform: str = "telegram") -> None:
        """Add chat to whitelist.

        Args:
            chat_id: The ID of the chat.
            platform: Messaging platform identifier.

        """
        await self._chat_service.update_whitelist_status(chat_id, platform, is_whitelisted=True)

    async def remove_from_whitelist(self, chat_id: int, platform: str = "telegram") -> None:
        """Remove chat from whitelist.

        Args:
            chat_id: The ID of the chat.
            platform: Messaging platform identifier.

        """
        await self._chat_service.update_whitelist_status(chat_id, platform, is_whitelisted=False)
