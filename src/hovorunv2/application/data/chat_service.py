"""Application service for low-level chat data operations."""

import json

from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.domain.chat import ChatDB
from hovorunv2.infrastructure.repositories.chat_repository import SQLAlchemyChatRepository


class ChatService:
    """Service to handle database transactions for chat-related entities."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        """Initialize with session maker."""
        self._session_maker = session_maker

    async def get_chat(self, chat_id: int, platform: str) -> ChatDB | None:
        """Fetch chat from database."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            return await repo.get_by_id(chat_id, platform)

    async def update_whitelist_status(self, chat_id: int, platform: str, *, is_whitelisted: bool) -> None:
        """Update whitelist status for a chat."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform)
            if chat:
                chat.is_whitelisted = is_whitelisted
            else:
                chat = ChatDB(chat_id=chat_id, platform=platform, is_whitelisted=is_whitelisted)
            await repo.save(chat)
            await session.commit()

    async def update_translation_settings(
        self, chat_id: int, platform: str, target_lang: str, ignored_langs: list[str]
    ) -> None:
        """Update translation settings for a chat."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform)
            if not chat:
                chat = ChatDB(chat_id=chat_id, platform=platform, is_whitelisted=False)

            chat.target_lang = target_lang
            chat.ignored_langs = json.dumps(ignored_langs)
            await repo.save(chat)
            await session.commit()

    async def get_all_whitelisted(self, platform: str) -> list[ChatDB]:
        """Fetch all whitelisted chats from database."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            return await repo.get_all_whitelisted(platform)

    async def get_or_create_chat(self, chat_id: int, platform: str = "telegram") -> ChatDB:
        """Fetch chat from database or create it if not exists."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform)
            if not chat:
                chat = ChatDB(chat_id=chat_id, platform=platform)
                await repo.save(chat)
                await session.commit()
            return chat

    async def update_chat(self, chat_id: int, platform: str = "telegram", **kwargs: str | bool | None) -> None:
        """Update arbitrary chat attributes."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform)
            if not chat:
                chat = ChatDB(chat_id=chat_id, platform=platform)

            for key, value in kwargs.items():
                setattr(chat, key, value)

            await repo.save(chat)
            await session.commit()
