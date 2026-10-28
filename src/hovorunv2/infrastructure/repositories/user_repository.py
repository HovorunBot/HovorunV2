"""SQLAlchemy repository for lifecycle-aware user entity."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.domain.enums import Platform
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.policy_queries import active_user


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
        stmt = active_user(telegram_id, platform)
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
    ) -> tuple[bool, bool, int | None]:
        """Atomically change trust and advance its durable reconciliation revision."""
        revision = await self._session.scalar(
            update(UserDB)
            .where(
                UserDB.telegram_id == telegram_id,
                UserDB.platform == Platform(platform),
                UserDB.deleted_at.is_(None),
                UserDB.is_trusted != is_trusted,
            )
            .values(is_trusted=is_trusted, trust_revision=UserDB.trust_revision + 1)
            .returning(UserDB.trust_revision)
        )
        if revision is not None:
            return True, True, revision
        user = await self.get_by_telegram_id(telegram_id, platform)
        return user is not None, False, None
