"""Persistence adapter for system state operations."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.ports import AuditedUnitOfWork, SystemStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.repositories.system_repository import SQLAlchemySystemRepository


class SQLAlchemySystemStore(SystemStore):
    """SQLAlchemy persistence store for system state with audited mutations."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
    ) -> None:
        """Initialize with session maker and audited unit of work."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def get_last_notified_version(self) -> str | None:
        """Fetch last notified version from database."""
        return await self.get_value("last_notified_version")

    async def set_last_notified_version(self, version: str, actor: ActorContext | None = None) -> None:
        """Update last notified version in database."""
        await self.set_value("last_notified_version", version, actor=actor)

    async def get_value(self, key: str) -> str | None:
        """Fetch arbitrary value from system repository."""
        async with self._session_maker() as session:
            repo = SQLAlchemySystemRepository(session)
            return await repo.get_value(key)

    async def _execute_set_value(self, session: AsyncSession, key: str, value: str) -> None:
        """Execute setting a value on persistence session."""
        repo = SQLAlchemySystemRepository(session)
        await repo.set_value(key, value)

    async def set_value(self, key: str, value: str, actor: ActorContext | None = None) -> None:
        """Store arbitrary value in system repository."""
        operation = partial(self._execute_set_value, key=key, value=value)

        await self._audited_uow.execute(
            actor=actor or ActorContext.system("set_system_value"),
            target_type="system_setting",
            target_id=key,
            action="set_value",
            operation=operation,
            changed_fields={key: value},
        )
