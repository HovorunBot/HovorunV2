"""Read-only dependant counts used by the recovery preview."""

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import FromClause

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.identity.recovery import RecoveryStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.beta import BetaEntitlementDB
from hovorunv2.domain.chat import ChatDB, chat_commands
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import ChatStatus, Platform, Role
from hovorunv2.domain.group_beta import GroupBetaEnablerDB, GroupBetaFeatureDB
from hovorunv2.domain.group_grant import GroupApprovalDB
from hovorunv2.domain.group_route import GroupRouteDB
from hovorunv2.domain.membership import GroupMembershipStateDB, MemberEvidenceDB
from hovorunv2.domain.notification import NotificationPreferenceDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation


class SQLAlchemyRecoveryStore(RecoveryStore):
    """Count retained rows without exposing their content."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def user_dependants(self, user_id: int, telegram_id: int) -> dict[str, int]:
        """Count grants, memberships, group support and preferences for a tombstoned user."""
        async with self._session_maker() as session:
            return {
                "beta_grants": await self._count(
                    session,
                    BetaEntitlementDB,
                    BetaEntitlementDB.user_id == user_id,
                    BetaEntitlementDB.revoked_at.is_(None),
                    BetaEntitlementDB.deleted_at.is_(None),
                ),
                "memberships": await self._count(
                    session,
                    MemberEvidenceDB,
                    MemberEvidenceDB.user_id == user_id,
                    MemberEvidenceDB.deleted_at.is_(None),
                ),
                "group_adminships": await self._count(
                    session,
                    GroupBetaEnablerDB,
                    GroupBetaEnablerDB.user_id == user_id,
                    GroupBetaEnablerDB.revoked_at.is_(None),
                    GroupBetaEnablerDB.deleted_at.is_(None),
                ),
                "notification_preferences": await self._count(
                    session,
                    NotificationPreferenceDB,
                    NotificationPreferenceDB.user_id == user_id,
                    NotificationPreferenceDB.deleted_at.is_(None),
                ),
            }

    async def group_dependants(self, chat_id: int) -> dict[str, int]:
        """Count retained selections, membership state, preferences and approvals."""
        async with self._session_maker() as session:
            return {
                "commands": await self._count(
                    session,
                    chat_commands,
                    chat_commands.c.chat_id == chat_id,
                    chat_commands.c.deleted_at.is_(None),
                ),
                "beta_selections": await self._count(
                    session,
                    GroupBetaFeatureDB,
                    GroupBetaFeatureDB.group_id == chat_id,
                    GroupBetaFeatureDB.deleted_at.is_(None),
                ),
                "membership_state": await self._count(
                    session,
                    GroupMembershipStateDB,
                    GroupMembershipStateDB.group_id == chat_id,
                    GroupMembershipStateDB.deleted_at.is_(None),
                ),
                "notification_preferences": await self._count(
                    session,
                    NotificationPreferenceDB,
                    NotificationPreferenceDB.group_id == chat_id,
                    NotificationPreferenceDB.deleted_at.is_(None),
                ),
                "approvals": await self._count(
                    session,
                    GroupApprovalDB,
                    GroupApprovalDB.group_id == chat_id,
                    GroupApprovalDB.deleted_at.is_(None),
                ),
            }

    async def group_commands(self, chat_id: int) -> tuple[str, ...]:
        """List active command associations for the group."""
        async with self._session_maker() as session:
            rows = await session.scalars(
                select(CommandDB.name)
                .join(chat_commands, CommandDB.id == chat_commands.c.command_id)
                .where(chat_commands.c.chat_id == chat_id, chat_commands.c.deleted_at.is_(None))
                .order_by(CommandDB.name)
            )
            return tuple(rows.all())

    async def _count(self, session: AsyncSession, model: FromClause | type, *conditions: ColumnElement[bool]) -> int:
        statement = select(func.count()).select_from(model)
        for condition in conditions:
            statement = statement.where(condition)
        return int(await session.scalar(statement) or 0)

    async def restore_user(self, telegram_id: int, revision: int, actor: ActorContext) -> int:
        """Reactivate the tombstone as a new generation, preserving prior role/trust/settings."""

        async def operation(session: AsyncSession) -> tuple[int, dict[str, object]]:
            await lock_async_mutation(session, MutationLocks(users=(telegram_id,)))
            row = await self._tombstone_user(session, telegram_id)
            if row is None or row.generation != revision:
                msg = "Deleted user revision changed; preview again."
                raise ValueError(msg)
            previous = row.generation
            row.deleted_at = None
            row.deleted_by_actor_id = None
            row.deleted_reason = None
            row.generation += 1
            await session.flush()
            return row.generation, {"generation": [previous, row.generation]}

        result: tuple[int, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="user",
            target_id=str(telegram_id),
            action="restore",
            operation=operation,
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda _outcome: True,
        )
        return result[0]

    async def fresh_user(self, telegram_id: int, actor: ActorContext) -> int:
        """Create a fresh USER generation while retaining the tombstone and prior history."""

        async def operation(session: AsyncSession) -> tuple[int, dict[str, object]]:
            await lock_async_mutation(session, MutationLocks(users=(telegram_id,)))
            active = await session.scalar(
                select(UserDB.id).where(UserDB.telegram_id == telegram_id, UserDB.deleted_at.is_(None))
            )
            if active is not None:
                msg = "An active identity already exists at this ID."
                raise ValueError(msg)
            latest = await session.scalar(select(func.max(UserDB.generation)).where(UserDB.telegram_id == telegram_id))
            generation = int(latest or 0) + 1
            session.add(
                UserDB(
                    telegram_id=telegram_id,
                    platform=Platform.TELEGRAM,
                    role=Role.USER,
                    is_trusted=False,
                    generation=generation,
                )
            )
            await session.flush()
            return generation, {"generation": generation}

        result: tuple[int, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="user",
            target_id=str(telegram_id),
            action="recover_fresh",
            operation=operation,
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda _outcome: True,
        )
        return result[0]

    async def restore_group(self, telegram_id: int, revision: int, actor: ActorContext) -> int:
        """Reactivate the group tombstone, preserving durable approval and retained settings."""

        async def operation(session: AsyncSession) -> tuple[int, dict[str, object]]:
            await lock_async_mutation(session, MutationLocks(chats=(telegram_id,)))
            row = await self._tombstone_group(session, telegram_id)
            if row is None or row.generation != revision:
                msg = "Deleted group revision changed; preview again."
                raise ValueError(msg)
            previous = row.generation
            row.deleted_at = None
            row.deleted_by_actor_id = None
            row.deleted_reason = None
            row.generation += 1
            await session.flush()
            return row.generation, {"generation": [previous, row.generation]}

        result: tuple[int, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="chat",
            target_id=str(telegram_id),
            action="restore",
            operation=operation,
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda _outcome: True,
        )
        return result[0]

    async def fresh_group(self, telegram_id: int, actor: ActorContext) -> int:
        """Create an unapproved fresh group generation without prior settings or selections."""

        async def operation(session: AsyncSession) -> tuple[int, dict[str, object]]:
            await lock_async_mutation(session, MutationLocks(chats=(telegram_id,)))
            active = await session.scalar(
                select(ChatDB.id).where(ChatDB.chat_id == telegram_id, ChatDB.deleted_at.is_(None))
            )
            if active is not None:
                msg = "An active group already exists at this ID."
                raise ValueError(msg)
            latest = await session.scalar(select(func.max(ChatDB.generation)).where(ChatDB.chat_id == telegram_id))
            generation = int(latest or 0) + 1
            session.add(
                ChatDB(
                    chat_id=telegram_id,
                    platform=Platform.TELEGRAM,
                    status=ChatStatus.UNAUTHORIZED,
                    generation=generation,
                )
            )
            await session.flush()
            return generation, {"generation": generation}

        result: tuple[int, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="chat",
            target_id=str(telegram_id),
            action="recover_fresh",
            operation=operation,
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda _outcome: True,
        )
        return result[0]

    async def _tombstone_user(self, session: AsyncSession, telegram_id: int) -> UserDB | None:
        return await session.scalar(
            select(UserDB)
            .where(UserDB.telegram_id == telegram_id, UserDB.deleted_at.is_not(None))
            .order_by(UserDB.id.desc())
        )

    async def _tombstone_group(self, session: AsyncSession, telegram_id: int) -> ChatDB | None:
        row = await session.scalar(
            select(ChatDB)
            .where(ChatDB.chat_id == telegram_id, ChatDB.deleted_at.is_not(None))
            .order_by(ChatDB.id.desc())
        )
        if row is not None:
            return row
        route = await session.scalar(select(GroupRouteDB).where(GroupRouteDB.telegram_id == telegram_id))
        if route is None:
            return None
        routed = await session.get(ChatDB, route.group_id)
        return routed if routed is not None and routed.deleted_at is not None else None
