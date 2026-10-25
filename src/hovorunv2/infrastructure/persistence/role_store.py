"""Implementation of RoleStore using SQLAlchemy and audited unit of work."""

from functools import partial
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.ports import AuditedUnitOfWork, RoleStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Platform, Role
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.role_repository import SQLAlchemyRoleRepository
from hovorunv2.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


class SQLAlchemyRoleStore(RoleStore):
    """Concrete RoleStore managing single user authority role with audit logging."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
    ) -> None:
        """Initialize store with session maker and audited unit of work."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def get_role(self, telegram_id: int, platform: Platform = Platform.TELEGRAM) -> Role | None:
        """Fetch active user's single authority role."""
        async with self._session_maker() as session:
            repo = SQLAlchemyRoleRepository(session)
            return await repo.get_role_by_telegram_id(telegram_id, platform)

    async def list_roles(self, role: Role | None = None) -> list[UserDB]:
        """List current authority from active user records."""
        async with self._session_maker() as session:
            return await SQLAlchemyRoleRepository(session).get_all_by_role(role)

    async def _execute_set_role(
        self,
        session: AsyncSession,
        telegram_id: int,
        role: Role,
        platform: Platform,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Execute role assignment directly on UserDB within an audited transaction."""
        user_repo = SQLAlchemyUserRepository(session)
        user = await user_repo.get_by_telegram_id(telegram_id, str(platform))
        if not user:
            user, _ = await user_repo.ingest(telegram_id, str(platform))
            if not user:
                return False, None
            old_role = None
        else:
            old_role = user.role

        if old_role == role:
            return False, None

        user.role = role
        await session.flush()
        old_val = old_role.value if old_role is not None else None
        return True, {"role": [old_val, role.value], "user_id": user.id}

    async def set_role(
        self,
        telegram_id: int,
        role: Role,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> bool:
        """Set user's single authority role with audit."""
        op = partial(
            self._execute_set_role,
            telegram_id=telegram_id,
            role=role,
            platform=platform,
        )
        result: tuple[bool, dict[str, Any] | None] = await self._audited_uow.execute(
            actor=actor,
            target_type="user_role",
            target_id=f"{telegram_id}:{role.name.lower()}",
            action="set_role",
            operation=op,
            changed_fields=lambda res: res[1] if res else None,
            audit_if=lambda res: res is not None and res[0] is True,
        )
        return result[0]
