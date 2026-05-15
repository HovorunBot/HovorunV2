"""Implementation of Chat repository using SQLAlchemy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from hovorunv2.application.data.constants import ChatStatus
from hovorunv2.domain.chat import ChatDB


class SQLAlchemyChatRepository:
    """Concrete repository implementation for SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a session."""
        self.session = session

    async def get_by_id(self, chat_id: int, platform: str = "telegram") -> ChatDB | None:
        """Fetch chat from database."""
        stmt = (
            select(ChatDB)
            .where(ChatDB.chat_id == chat_id, ChatDB.platform == platform)
            .options(selectinload(ChatDB.commands))
        )
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
            db_chat.status = chat.status
        else:
            self.session.add(chat)

    async def get_all_by_status(self, status: ChatStatus, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all chats with a specific status for a given platform."""
        stmt = select(ChatDB).where(ChatDB.status == status, ChatDB.platform == platform)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
