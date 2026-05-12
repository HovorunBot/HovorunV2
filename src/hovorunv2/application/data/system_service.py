"""Application service for system-wide state operations."""

from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.infrastructure.repositories.system_repository import SQLAlchemySystemRepository


class SystemDataService:
    """Service to handle database transactions for system state."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        """Initialize with session maker."""
        self._session_maker = session_maker

    async def get_last_notified_version(self) -> str | None:
        """Fetch last notified version from database."""
        async with self._session_maker() as session:
            repo = SQLAlchemySystemRepository(session)
            return await repo.get_value("last_notified_version")

    async def set_last_notified_version(self, version: str) -> None:
        """Update last notified version in database."""
        await self.set_value("last_notified_version", version)

    async def get_value(self, key: str) -> str | None:
        """Fetch arbitrary value from system repository."""
        async with self._session_maker() as session:
            repo = SQLAlchemySystemRepository(session)
            return await repo.get_value(key)

    async def set_value(self, key: str, value: str) -> None:
        """Store arbitrary value in system repository."""
        async with self._session_maker() as session:
            repo = SQLAlchemySystemRepository(session)
            await repo.set_value(key, value)
            await session.commit()
