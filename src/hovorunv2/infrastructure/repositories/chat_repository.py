"""Implementation of Chat repository using SQLAlchemy."""

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform
from hovorunv2.domain.group_route import GroupRouteDB


class SQLAlchemyChatRepository:
    """Concrete repository implementation for SQLAlchemy with active-only filtering."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a session."""
        self.session = session

    async def get_by_id(self, chat_id: int, platform: str = "telegram") -> ChatDB | None:
        """Fetch active chat from database."""
        stmt = (
            select(ChatDB)
            .where(
                or_(
                    ChatDB.chat_id == chat_id,
                    ChatDB.id.in_(select(GroupRouteDB.group_id).where(GroupRouteDB.telegram_id == chat_id)),
                ),
                ChatDB.platform == Platform(platform),
                ChatDB.deleted_at.is_(None),
            )
            .options(selectinload(ChatDB.commands))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_groups(self, after_id: int, limit: int, *, present_only: bool) -> list[ChatDB]:
        """Bound candidate discovery and exclude private/deleted identities and route aliases."""
        if limit < 1:
            return []
        query = select(ChatDB).where(
            ChatDB.id > after_id,
            ChatDB.chat_id < 0,
            ChatDB.chat_type.in_(("group", "supergroup")),
            ChatDB.platform == Platform.TELEGRAM,
            ChatDB.deleted_at.is_(None),
        )
        if present_only:
            query = query.where(ChatDB.is_bot_present.is_(True))
        rows = await self.session.scalars(query.order_by(ChatDB.id).limit(limit))
        return list(rows.all())

    async def get_deleted_by_chat_id(self, chat_id: int, platform: str = "telegram") -> ChatDB | None:
        """Fetch latest soft-deleted chat tombstone."""
        stmt = (
            select(ChatDB)
            .where(
                or_(
                    ChatDB.chat_id == chat_id,
                    ChatDB.id.in_(select(GroupRouteDB.group_id).where(GroupRouteDB.telegram_id == chat_id)),
                ),
                ChatDB.platform == Platform(platform),
                ChatDB.deleted_at.is_not(None),
            )
            .order_by(ChatDB.generation.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_historical_by_chat_id(self, chat_id: int, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all chat generations (active and deleted) for recovery workflows."""
        stmt = (
            select(ChatDB)
            .where(
                ChatDB.chat_id == chat_id,
                ChatDB.platform == Platform(platform),
            )
            .order_by(ChatDB.generation.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def add(self, chat: ChatDB) -> None:
        """Add a new chat entity to the database session."""
        self.session.add(chat)

    async def get_all_by_status(self, status: ChatStatus, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all active chats with a specific status for a given platform."""
        stmt = select(ChatDB).where(
            ChatDB.status == status,
            ChatDB.platform == Platform(platform),
            ChatDB.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(
        self,
        chat_id: int,
        actor_id: int | None,
        reason: str,
        platform: str = "telegram",
        deleted_at: datetime | None = None,
    ) -> bool:
        """Soft-delete an active chat identity."""
        chat = await self.get_by_id(chat_id, platform)
        if not chat:
            return False
        chat.deleted_at = deleted_at or datetime.now(UTC)
        chat.deleted_by_actor_id = actor_id
        chat.deleted_reason = reason
        return True
