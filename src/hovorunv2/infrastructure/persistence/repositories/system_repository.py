"""Implementation of System State repository using SQLAlchemy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.domain.system import SystemStateDB


class SQLAlchemySystemRepository:
    """Concrete repository implementation for system state."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a session."""
        self.session = session

    async def get_value(self, key: str) -> str | None:
        """Fetch value for a given key."""
        stmt = select(SystemStateDB).where(SystemStateDB.key == key)
        result = await self.session.execute(stmt)
        state = result.scalar_one_or_none()
        return state.value if state else None

    async def set_value(self, key: str, value: str) -> None:
        """Set value for a given key."""
        stmt = select(SystemStateDB).where(SystemStateDB.key == key)
        result = await self.session.execute(stmt)
        state = result.scalar_one_or_none()

        if state:
            state.value = value
        else:
            state = SystemStateDB(key=key, value=value)
            self.session.add(state)
