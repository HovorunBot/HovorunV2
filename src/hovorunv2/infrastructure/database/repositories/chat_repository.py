"""Implementation of Chat repository using SQLAlchemy."""

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from hovorunv2.infrastructure.database.models.chat import ChatDB

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SQLAlchemyChatRepository:
    """Concrete repository implementation for SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a session."""
        self.session = session

    async def get_by_id(self, chat_id: int, platform: str = "telegram") -> ChatDB | None:
        """Fetch chat from database."""
        stmt = select(ChatDB).where(ChatDB.chat_id == chat_id, ChatDB.platform == platform)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, chat: ChatDB) -> None:
        """Persist chat to database."""
        # Note: In most use cases with AsyncSession, chat object is already attached.
        # But if it's a new detached object, we need to merge or add it.
        stmt = select(ChatDB).where(ChatDB.chat_id == chat.chat_id, ChatDB.platform == chat.platform)
        result = await self.session.execute(stmt)
        db_chat = result.scalar_one_or_none()

        if db_chat:
            db_chat.is_whitelisted = chat.is_whitelisted
        else:
            self.session.add(chat)

        await self.session.commit()

    async def remove_from_whitelist(self, chat_id: int, platform: str = "telegram") -> None:
        """Remove chat from whitelist."""
        stmt = delete(ChatDB).where(ChatDB.chat_id == chat_id, ChatDB.platform == platform)
        await self.session.execute(stmt)
        await self.session.commit()
