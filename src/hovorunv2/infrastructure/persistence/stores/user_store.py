"""Persistence adapter for lifecycle-aware user identity operations."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ActorOrigin, Role
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository


class SQLAlchemyUserStore(UserStore):
    """SQLAlchemy persistence store for user identity with lifecycle and audit handling."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
        policy: AuthorizationPolicy,
    ) -> None:
        """Initialize with session maker and audited unit of work."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._policy = policy

    async def get_by_id(self, user_id: int) -> UserDB | None:
        """Fetch active user by internal entity ID."""
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            return await repo.get_by_id(user_id)

    async def get_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> UserDB | None:
        """Fetch active user by platform and numeric Telegram ID."""
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            return await repo.get_by_telegram_id(telegram_id, platform)

    async def get_historical_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> list[UserDB]:
        """Fetch all generations of user identity for explicit recovery workflows."""
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            return await repo.get_historical_by_telegram_id(telegram_id, platform)

    async def _execute_ingest(
        self,
        session: AsyncSession,
        telegram_id: int,
        platform: str,
    ) -> tuple[UserDB | None, bool]:
        """Execute user ingestion on session."""
        await lock_async_mutation(session, MutationLocks(users=(telegram_id,), platform=platform))
        repo = SQLAlchemyUserRepository(session)
        return await repo.ingest(telegram_id, platform)

    async def _execute_soft_delete(
        self,
        session: AsyncSession,
        telegram_id: int,
        actor: ActorContext,
        reason: str,
        platform: str,
        deleted_at: datetime,
    ) -> bool:
        """Execute user soft-deletion on session."""
        await lock_async_mutation(session, MutationLocks(users=(telegram_id,), platform=platform))
        repo = SQLAlchemyUserRepository(session)
        target = await repo.get_by_telegram_id(telegram_id, platform)
        if actor.origin != ActorOrigin.CLI and target is not None and target.role >= Role.ADMIN:
            msg = "Protected administrators can only be deleted through CLI."
            raise PermissionError(msg)
        return await repo.soft_delete(
            telegram_id,
            actor.actor_id,
            reason,
            platform,
            deleted_at,
        )

    async def _execute_set_trust(
        self,
        session: AsyncSession,
        telegram_id: int,
        platform: str,
        *,
        is_trusted: bool,
        actor: ActorContext,
    ) -> tuple[bool, bool, int | None]:
        """Execute user trust update on session."""
        await lock_async_mutation(
            session,
            MutationLocks(
                users=(telegram_id,), actors=(actor.actor_id,) if actor.actor_id is not None else (), platform=platform
            ),
        )
        repo = SQLAlchemyUserRepository(session)
        if actor.origin == ActorOrigin.TELEGRAM:
            operator = await repo.get_by_telegram_id(actor.actor_id or 0, platform)
            if operator is None or not self._policy.is_allowed(
                PermissionFacts(operator.role, "bot:administer", PermissionTarget("bot"))
            ):
                msg = "Trust administration requires current bot administrator authority."
                raise PermissionError(msg)
        return await repo.set_trust(telegram_id, is_trusted=is_trusted, platform=platform)

    async def ingest_user(
        self, telegram_id: int, platform: str = "telegram", actor: ActorContext | None = None
    ) -> UserDB | None:
        """Ingest user from incoming evidence without resurrecting deleted identities."""
        existing_active = await self.get_by_telegram_id(telegram_id, platform)
        if existing_active:
            return existing_active

        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            tombstone = await repo.get_deleted_by_telegram_id(telegram_id, platform)
            if tombstone:
                return None

        operation = partial(
            self._execute_ingest,
            telegram_id=telegram_id,
            platform=platform,
        )

        res: tuple[UserDB | None, bool] = await self._audited_uow.execute(
            actor=actor or ActorContext.system("user_ingestion"),
            target_type="user",
            target_id=telegram_id,
            action="create",
            operation=operation,
            changed_fields={"telegram_id": telegram_id, "platform": platform, "generation": 1},
            audit_if=lambda r: r[1],
        )
        return res[0]

    async def soft_delete_user(
        self, telegram_id: int, actor: ActorContext, reason: str, platform: str = "telegram"
    ) -> bool:
        """Soft-delete an active user identity recording actor and deletion reason."""
        now = datetime.now(UTC)
        operation = partial(
            self._execute_soft_delete,
            telegram_id=telegram_id,
            actor=actor,
            reason=reason,
            platform=platform,
            deleted_at=now,
        )

        return await self._audited_uow.execute(
            actor=actor,
            target_type="user",
            target_id=telegram_id,
            action="delete",
            operation=operation,
            changed_fields={"deleted_at": now.isoformat(), "reason": reason},
            audit_if=bool,
        )

    async def set_trust(
        self, telegram_id: int, *, is_trusted: bool, actor: ActorContext | None = None, platform: str = "telegram"
    ) -> bool:
        """Update trust flag for an active user identity."""
        operation = partial(
            self._execute_set_trust,
            telegram_id=telegram_id,
            platform=platform,
            is_trusted=is_trusted,
            actor=actor or ActorContext.system("set_trust"),
        )

        res: tuple[bool, bool, int | None] = await self._audited_uow.execute(
            actor=actor or ActorContext.system("set_trust"),
            target_type="user",
            target_id=telegram_id,
            action="set_trust",
            operation=operation,
            changed_fields=lambda result: {
                "is_trusted": [not is_trusted, is_trusted],
                "trust_revision": [result[2] - 1, result[2]],
            },
            audit_if=lambda r: r[1],
        )
        return res[0]
