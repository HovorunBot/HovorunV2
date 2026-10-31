"""Audited membership persistence adapter with bounded freshness queries."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.membership import GroupMembershipStateDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.membership_repository import (
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
        """List a bounded batch of missing/expired evidence subjects.

        Args:
            now: Evaluation timestamp for freshness bounds.
            limit: Maximum number of groups to return.

        Returns:
            Groups with missing or expired bot, admin or relevant-user evidence.
        """
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).due_groups(now, limit)

    async def relevant_users(self, group_id: int, limit: int = 50) -> list[UserDB]:
        """List only active administrator candidates with a SQL bound.

        Args:
            group_id: Internal group entity ID.
            limit: Maximum number of users to return.

        Returns:
            Current and unresolved administrator candidates ordered by expiry.
        """
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).relevant_users(group_id, limit)

    async def full_banned_admin_status(self, group_id: int, now: datetime) -> MembershipStatus | None:
        """Query complete current-generation blockers independently of maintenance batch sizes.

        Args:
            group_id: Internal group entity ID.
            now: Evaluation timestamp for freshness bounds.

        Returns:
            Admin status of the current banned-admin candidates, or None if absent.
        """
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).full_banned_admin_status(group_id, now)

    async def get_member(self, group_id: int, user_id: int) -> MembershipEvidence | None:
        """Read evidence with explicit verification and expiry times.

        Args:
            group_id: Internal group entity ID.
            user_id: Internal user entity ID.

        Returns:
            Membership evidence with explicit verification and expiry times, or None.
        """
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).get_member(group_id, user_id)

    async def get_state(self, group_id: int) -> GroupMembershipStateDB | None:
        """Read generation-bound policy revision and bot/admin freshness.

        Args:
            group_id: Internal group entity ID.

        Returns:
            Current active membership state row, or None if absent.
        """
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).get_state(group_id)

    async def is_admin_any(self, user_telegram_id: int, now: datetime) -> bool:
        """Check if user is verified administrator in any active group.

        Args:
            user_telegram_id: Numeric Telegram ID of the user.
            now: Evaluation timestamp for freshness bounds.

        Returns:
            True if the user is a verified administrator of any active group.
        """
        async with self._session_maker() as session:
            return await SQLAlchemyMembershipRepository(session).is_admin_any(user_telegram_id, now)

    async def _execute(
        self,
        session: AsyncSession,
        operation: Callable[[SQLAlchemyMembershipRepository], Awaitable[MembershipMutation]],
        locks: MutationLocks,
    ) -> MembershipMutation:
        """Run one repository operation under the declared locks."""
        await lock_async_mutation(session, locks)
        return await operation(SQLAlchemyMembershipRepository(session))

    async def _mutate(
        self,
        group_id: int,
        action: str,
        operation: Callable[[SQLAlchemyMembershipRepository], Awaitable[MembershipMutation]],
        actor: ActorContext | None,
        users: tuple[int, ...] = (),
    ) -> bool:
        """Run one audited membership mutation and report whether state changed."""
        result: MembershipMutation = await self._audited_uow.execute(
            actor=actor or ActorContext.system("membership verification"),
            target_type="membership",
            target_id=group_id,
            action=action,
            operation=partial(
                self._execute, operation=operation, locks=MutationLocks(group_ids=(group_id,), users=users)
            ),
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
        """Save relevant newer member evidence and invalidate policy snapshots.

        Args:
            group_id: Internal group entity ID.
            evidence: Membership evidence to persist.
            actor: Optional actor context recorded on the audit event.
            expected_user_id: Optional internal user ID the evidence must match.

        Returns:
            True if the evidence changed durable state.
        """
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
            users=(evidence.user_telegram_id,),
        )

    async def save_bot(self, group_id: int, evidence: BotCapabilityEvidence, actor: ActorContext | None = None) -> bool:
        """Save per-capability verification separately from physical presence and grants.

        Args:
            group_id: Internal group entity ID.
            evidence: Bot capability evidence to persist.
            actor: Optional actor context recorded on the audit event.

        Returns:
            True if the evidence changed durable state.
        """
        return await self._mutate(
            group_id,
            "verify_bot",
            partial(SQLAlchemyMembershipRepository.save_bot, group_id=group_id, evidence=evidence),
            actor,
        )

    async def save_admins(
        self, group_id: int, evidence: AdministratorEvidence, actor: ActorContext | None = None
    ) -> bool:
        """Save an administrator snapshot without treating uncertainty as absence.

        Args:
            group_id: Internal group entity ID.
            evidence: Administrator snapshot evidence to persist.
            actor: Optional actor context recorded on the audit event.

        Returns:
            True if the evidence changed durable state.
        """
        return await self._mutate(
            group_id,
            "verify_admins",
            partial(SQLAlchemyMembershipRepository.save_admins, group_id=group_id, evidence=evidence),
            actor,
            users=tuple(member.user_telegram_id for member in evidence.members),
        )
