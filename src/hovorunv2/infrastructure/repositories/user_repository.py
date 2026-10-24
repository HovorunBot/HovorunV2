"""SQLAlchemy repository for lifecycle-aware user entity."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.domain.enums import Platform
from hovorunv2.domain.user import UserDB


class SQLAlchemyUserRepository:
    """Repository managing user persistence with active-only filtering by default."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session."""
        self._session = session

    async def get_by_id(self, user_id: int) -> UserDB | None:
        """Fetch active user by internal entity ID."""
        stmt = select(UserDB).where(UserDB.id == user_id, UserDB.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> UserDB | None:
        """Fetch active user by platform and numeric Telegram ID."""
        stmt = select(UserDB).where(
            UserDB.platform == Platform(platform),
            UserDB.telegram_id == telegram_id,
            UserDB.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_deleted_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> UserDB | None:
        """Fetch latest soft-deleted user tombstone by platform and numeric Telegram ID."""
        stmt = (
            select(UserDB)
            .where(
                UserDB.platform == Platform(platform),
                UserDB.telegram_id == telegram_id,
                UserDB.deleted_at.is_not(None),
            )
            .order_by(UserDB.generation.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_historical_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> list[UserDB]:
        """Fetch all generations (active and deleted) for explicit recovery workflows."""
        stmt = (
            select(UserDB)
            .where(
                UserDB.platform == Platform(platform),
                UserDB.telegram_id == telegram_id,
            )
            .order_by(UserDB.generation.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    def add(self, user: UserDB) -> None:
        """Add user to persistence session."""
        self._session.add(user)

    async def ingest(self, telegram_id: int, platform: str = "telegram") -> tuple[UserDB | None, bool]:
        """Ingest user if not active and not tombstoned."""
        active = await self.get_by_telegram_id(telegram_id, platform)
        if active:
            return active, False
        deleted = await self.get_deleted_by_telegram_id(telegram_id, platform)
        if deleted:
            return None, False

        new_user = UserDB(
            telegram_id=telegram_id,
            platform=Platform(platform),
            is_trusted=False,
            generation=1,
        )
        self.add(new_user)
        return new_user, True

    async def soft_delete(
        self,
        telegram_id: int,
        actor_id: int | None,
        reason: str,
        platform: str = "telegram",
        deleted_at: datetime | None = None,
    ) -> bool:
        """Soft-delete an active user identity."""
        user = await self.get_by_telegram_id(telegram_id, platform)
        if not user:
            return False
        user.deleted_at = deleted_at or datetime.now(UTC)
        user.deleted_by_actor_id = actor_id
        user.deleted_reason = reason
        return True

    async def set_trust(
        self,
        telegram_id: int,
        *,
        is_trusted: bool,
        platform: str = "telegram",
    ) -> tuple[bool, bool]:
        """Update trust flag for an active user."""
        user = await self.get_by_telegram_id(telegram_id, platform)
        if not user:
            return False, False
        if user.is_trusted == is_trusted:
            return True, False
        user.is_trusted = is_trusted
        return True, True
