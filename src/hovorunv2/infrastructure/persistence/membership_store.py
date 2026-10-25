"""Audited membership persistence adapter with bounded freshness queries."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.ports import AuditedUnitOfWork
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.membership import GroupMembershipStateDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.membership_repository import (
    MembershipMutation,
    SQLAlchemyMembershipRepository,
)


class SQLAlchemyMembershipStore(MembershipStore):
    """Bind each accepted evidence update and revision invalidation to one audit transaction."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        """Receive mandatory database and audit boundaries."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def due_groups(self, now: datetime, limit: int = 50) -> list[ChatDB]:
        """List a bounded batch of missing/expired evidence subjects."""
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).due_groups(now, limit)

    async def relevant_users(self, group_id: int, limit: int = 50) -> list[UserDB]:
        """List only active administrator candidates with a SQL bound."""
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).relevant_users(group_id, limit)

    async def full_banned_admin_status(self, group_id: int, now: datetime) -> MembershipStatus | None:
        """Query complete current-generation blockers independently of maintenance batch sizes."""
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).full_banned_admin_status(group_id, now)

    async def get_member(self, group_id: int, user_id: int) -> MembershipEvidence | None:
        """Read evidence with explicit verification and expiry times."""
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).get_member(group_id, user_id)

    async def get_state(self, group_id: int) -> GroupMembershipStateDB | None:
        """Read generation-bound policy revision and bot/admin freshness."""
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).get_state(group_id)

    async def is_admin_any(self, user_telegram_id: int, now: datetime) -> bool:
        """Check if user is verified administrator in any active group."""
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).is_admin_any(user_telegram_id, now)

    async def _execute(
        self,
        session: AsyncSession,
        operation: Callable[[SQLAlchemyMembershipRepository], Awaitable[MembershipMutation]],
    ) -> MembershipMutation:
        return await operation(SQLAlchemyMembershipRepository(session))

    async def _mutate(
        self,
        group_id: int,
        action: str,
        operation: Callable[[SQLAlchemyMembershipRepository], Awaitable[MembershipMutation]],
        actor: ActorContext | None,
    ) -> bool:
        result: MembershipMutation = await self._audited_uow.execute(
            actor=actor or ActorContext.system("membership verification"),
            target_type="membership",
            target_id=group_id,
            action=action,
            operation=partial(self._execute, operation=operation),
            changed_fields=lambda result: result.changes,
            audit_if=lambda result: result.changed,
        )
        return result.changed

    async def save_member(
        self,
        group_id: int,
        evidence: MembershipEvidence,
        actor: ActorContext | None = None,
        *,
        expected_user_id: int | None = None,
    ) -> bool:
        """Save relevant newer member evidence and invalidate policy snapshots."""
        return await self._mutate(
            group_id,
            "verify_member",
            partial(
                SQLAlchemyMembershipRepository.save_member,
                group_id=group_id,
                evidence=evidence,
                expected_user_id=expected_user_id,
            ),
            actor,
        )

    async def save_bot(self, group_id: int, evidence: BotCapabilityEvidence, actor: ActorContext | None = None) -> bool:
        """Save per-capability verification separately from physical presence and grants."""
        return await self._mutate(
            group_id,
            "verify_bot",
            partial(SQLAlchemyMembershipRepository.save_bot, group_id=group_id, evidence=evidence),
            actor,
        )

    async def save_admins(
        self, group_id: int, evidence: AdministratorEvidence, actor: ActorContext | None = None
    ) -> bool:
        """Save an administrator snapshot without treating uncertainty as absence."""
        return await self._mutate(
            group_id,
            "verify_admins",
            partial(SQLAlchemyMembershipRepository.save_admins, group_id=group_id, evidence=evidence),
            actor,
        )
