"""SQLAlchemy repository for audit events."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.domain.audit import AuditEventDB


class SQLAlchemyAuditRepository:
    """Repository for appending and querying audit records."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with a session."""
        self.session = session

    async def add(self, event: AuditEventDB) -> None:
        """Add audit event to session (does not commit)."""
        self.session.add(event)

    async def get_by_target(self, target_type: str, target_id: str) -> list[AuditEventDB]:
        """Fetch audit events for a target ordered by ID ascending."""
        stmt = (
            select(AuditEventDB)
            .where(AuditEventDB.target_type == target_type, AuditEventDB.target_id == str(target_id))
            .order_by(AuditEventDB.id.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
