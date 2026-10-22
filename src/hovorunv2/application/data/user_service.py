"""Application service for low-level user data operations."""

from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.data.constants import UserTier
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


class UserDataService:
    """Service to handle database transactions for user-related entities."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        """Initialize with session maker."""
        self._session_maker = session_maker

    async def get_user(self, user_id: int, platform: str = "telegram") -> UserDB | None:
        """Fetch user from database."""
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            return await repo.get_by_id(user_id, platform)

    async def get_or_create_user(
        self,
        user_id: int,
        platform: str = "telegram",
        default_tier: UserTier = UserTier.USER,
    ) -> UserDB:
        """Fetch existing user or create a new user with default tier."""
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_id(user_id, platform)
            if not user:
                user = UserDB(
                    user_id=user_id,
                    platform=platform,
                    tier=int(default_tier),
                )
                await repo.save(user)
                await session.commit()
            return user

    async def set_tier(self, user_id: int, tier: UserTier, platform: str = "telegram") -> None:
        """Update authorization tier for a user."""
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_id(user_id, platform)
            if user:
                user.tier = int(tier)
            else:
                user = UserDB(
                    user_id=user_id,
                    platform=platform,
                    tier=int(tier),
                )
            await repo.save(user)
            await session.commit()

    async def get_admins(self, platform: str = "telegram") -> list[UserDB]:
        """Fetch all admin accounts for a platform."""
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            return await repo.get_all_by_tier(UserTier.ADMIN, platform)
