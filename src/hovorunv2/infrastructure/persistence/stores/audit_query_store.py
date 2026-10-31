"""Keyset-paginated, read-only audit history queries."""

from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.operations.audit_inspection import AuditQueryStore, AuditRecord
from hovorunv2.domain.audit import AuditEventDB


class SQLAlchemyAuditQueryStore(AuditQueryStore):
    """Read append-only events without modifying them."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def query(
        self,
        *,
        target_type: str | None,
        action: str | None,
        since: datetime | None,
        until: datetime | None,
        before: tuple[datetime, int] | None,
        limit: int,
    ) -> list[AuditRecord]:
        """Return at most `limit` rows in stable descending time/ID order."""
        statement = select(AuditEventDB)
        if target_type is not None:
            statement = statement.where(AuditEventDB.target_type == target_type)
        if action is not None:
            statement = statement.where(AuditEventDB.action == action)
        if since is not None:
            statement = statement.where(AuditEventDB.created_at >= since)
        if until is not None:
            statement = statement.where(AuditEventDB.created_at <= until)
        if before is not None:
            statement = statement.where(tuple_(AuditEventDB.created_at, AuditEventDB.id) < before)
        statement = statement.order_by(AuditEventDB.created_at.desc(), AuditEventDB.id.desc()).limit(limit)
        async with self._session_maker() as session:
            rows = (await session.scalars(statement)).all()
            return [self._record(row) for row in rows]

    def _record(self, row: AuditEventDB) -> AuditRecord:
        return AuditRecord(
            row.id,
            row.created_at,
            row.actor_origin.value,
            row.actor_id,
            row.target_type,
            row.target_id,
            row.action,
            row.changed_fields,
            row.reason,
            row.correlation_id,
        )
