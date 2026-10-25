"""Implementation of UserRole repository using SQLAlchemy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.domain.enums import Platform, Role
from hovorunv2.domain.user import UserDB


class SQLAlchemyRoleRepository:
    """Concrete repository for user roles querying active user authority."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a database session."""
        self.session = session

    async def get_role_by_telegram_id(self, telegram_id: int, platform: Platform = Platform.TELEGRAM) -> Role | None:
        """Fetch single active authority role directly from active user record."""
        stmt = select(UserDB.role).where(
            UserDB.platform == platform,
            UserDB.telegram_id == telegram_id,
            UserDB.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_by_role(self, role: Role | None = None, platform: Platform = Platform.TELEGRAM) -> list[UserDB]:
        """Fetch all active users with a specific role."""
        stmt = select(UserDB).where(
            UserDB.platform == platform,
            UserDB.role == role if role is not None else UserDB.role >= Role.ADMIN,
            UserDB.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt.order_by(UserDB.telegram_id))
        return list(result.scalars().all())
