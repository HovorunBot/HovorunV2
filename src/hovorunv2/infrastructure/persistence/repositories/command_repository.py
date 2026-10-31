"""Implementation of Command repository using SQLAlchemy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.domain.command import CommandDB


class SQLAlchemyCommandRepository:
    """Concrete repository implementation for commands."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a session."""
        self.session = session

    async def get_by_name(self, name: str) -> CommandDB | None:
        """Fetch the command by its unique name.

        Args:
            name: Unique command name.

        Returns:
            Command matching the name, or None if absent.
        """
        stmt = select(CommandDB).where(CommandDB.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
