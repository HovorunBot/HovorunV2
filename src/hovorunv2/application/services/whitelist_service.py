"""Application service for managing chat whitelisting."""

from typing import TYPE_CHECKING

from hovorunv2.infrastructure.database.models.chat import ChatDB

if TYPE_CHECKING:
    from hovorunv2.infrastructure.database.repositories.chat_repository import SQLAlchemyChatRepository


class WhitelistService:
    """Service to handle whitelisting business rules."""

    def __init__(self, chat_repository: SQLAlchemyChatRepository) -> None:
        """Initialize service with a repository."""
        self.chat_repository = chat_repository

    async def is_whitelisted(self, chat_id: int, platform: str = "telegram") -> bool:
        """Check if chat is whitelisted."""
        chat = await self.chat_repository.get_by_id(chat_id, platform)
        return chat.is_whitelisted if chat else False

    async def add_to_whitelist(self, chat_id: int, platform: str = "telegram") -> None:
        """Add chat to whitelist."""
        chat = await self.chat_repository.get_by_id(chat_id, platform)
        if chat:
            chat.is_whitelisted = True
        else:
            chat = ChatDB(chat_id=chat_id, is_whitelisted=True, platform=platform)
        await self.chat_repository.save(chat)

    async def remove_from_whitelist(self, chat_id: int, platform: str = "telegram") -> None:
        """Remove chat from whitelist."""
        await self.chat_repository.remove_from_whitelist(chat_id, platform)
