"""Implementation of PublicHandle repository using SQLAlchemy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.domain.enums import Platform, TargetKind
from hovorunv2.domain.handle import PublicHandleDB


class SQLAlchemyPublicHandleRepository:
    """Concrete repository for shared public-handle registry."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a database session."""
        self.session = session

    async def get_by_handle(
        self, handle_normalized: str, platform: Platform = Platform.TELEGRAM
    ) -> PublicHandleDB | None:
        """Fetch handle mapping by normalized username."""
        stmt = select(PublicHandleDB).where(
            PublicHandleDB.platform == platform,
            PublicHandleDB.handle_normalized == handle_normalized,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_target(
        self, target_kind: TargetKind, target_id: int, platform: Platform = Platform.TELEGRAM
    ) -> PublicHandleDB | None:
        """Fetch handle mapping for a specific numeric target entity."""
        stmt = select(PublicHandleDB).where(
            PublicHandleDB.platform == platform,
            PublicHandleDB.target_kind == target_kind,
            PublicHandleDB.target_id == target_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def add(self, handle: PublicHandleDB) -> None:
        """Add a new handle mapping to the database session."""
        self.session.add(handle)

    async def delete(self, handle: PublicHandleDB) -> None:
        """Delete a handle mapping from the database session."""
        await self.session.delete(handle)
