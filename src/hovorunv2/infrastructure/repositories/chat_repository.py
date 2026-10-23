"""Implementation of Chat repository using SQLAlchemy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus


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

    def add(self, chat: ChatDB) -> None:
        """Add a new chat entity to the database session."""
        self.session.add(chat)

    async def get_all_by_status(self, status: ChatStatus, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all chats with a specific status for a given platform."""
        stmt = select(ChatDB).where(ChatDB.status == status, ChatDB.platform == platform)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
