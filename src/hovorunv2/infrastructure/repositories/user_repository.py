"""Implementation of User repository using SQLAlchemy."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.data.constants import UserTier
from hovorunv2.domain.user import UserDB


class SQLAlchemyUserRepository:
    """Concrete user repository implementation for SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a session."""
        self.session = session

    async def get_by_id(self, user_id: int, platform: str = "telegram") -> UserDB | None:
        """Fetch user from database."""
        stmt = select(UserDB).where(UserDB.user_id == user_id, UserDB.platform == platform)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, user: UserDB) -> None:
        """Persist user to database."""
        stmt = select(UserDB).where(UserDB.user_id == user.user_id, UserDB.platform == user.platform)
        result = await self.session.execute(stmt)
        db_user = result.scalar_one_or_none()

        if db_user:
            db_user.tier = user.tier
        else:
            self.session.add(user)

    async def get_all_by_tier(self, tier: UserTier, platform: str = "telegram") -> list[UserDB]:
        """Fetch all users with a specific tier (or higher) for a given platform."""
        stmt = select(UserDB).where(UserDB.tier >= int(tier), UserDB.platform == platform)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_tier(self, tier: UserTier, platform: str = "telegram") -> int:
        """Count users with a specific tier (or higher) for a given platform."""
        stmt = select(func.count(UserDB.id)).where(UserDB.tier >= int(tier), UserDB.platform == platform)
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)
