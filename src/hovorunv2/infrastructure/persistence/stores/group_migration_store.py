"""Atomic routing migration preserving stable group identity, bans and audit linkage."""

from functools import partial

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import TargetKind
from hovorunv2.domain.group_route import GroupRouteDB
from hovorunv2.domain.handle import PublicHandleDB
from hovorunv2.domain.membership import GroupMembershipStateDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.chat_repository import SQLAlchemyChatRepository


class SQLAlchemyGroupMigrationStore:
    """Accept only routing evidence supplied by Telegram service-message handling."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        self._sessions = session_maker
        self._audit = audited_uow

    async def canonical_id(self, telegram_id: int) -> int | None:
        """Resolve aliases through an active group; deleted generations remain unavailable.

        Args:
            telegram_id: Numeric Telegram ID or route alias.

        Returns:
            Canonical numeric ID of the active group, or None if absent.
        """
        async with self._sessions() as session:
            group = await SQLAlchemyChatRepository(session).get_by_id(telegram_id)
            return group.chat_id if group is not None and group.chat_type in {"group", "supergroup"} else None

    async def migrate(self, old_id: int, new_id: int) -> bool:
        """Retain every internal association and reject conflicting target identities.

        Args:
            old_id: Numeric Telegram ID being migrated from.
            new_id: Numeric Telegram ID being migrated to.

        Returns:
            True if the route was migrated.

        Raises:
            ValueError: If the IDs are not two distinct negative Telegram IDs.
        """
        if old_id >= 0 or new_id >= 0 or old_id == new_id:
            msg = "Group migration requires two distinct negative Telegram IDs."
            raise ValueError(msg)
        result = await self._audit.execute(
            actor=ActorContext.system("telegram_group_migration"),
            target_type="chat",
            target_id=str(old_id),
            action="migrate_route",
            operation=partial(self._migrate, old_id=old_id, new_id=new_id),
            changed_fields=lambda result: {"group_id": result[1], "telegram_id": [old_id, new_id]},
            audit_if=lambda result: result[0],
        )
        return result[0]

    async def _migrate(self, session: AsyncSession, old_id: int, new_id: int) -> tuple[bool, int]:
        """Perform the route migration inside the locked, audited transaction."""
        await lock_async_mutation(session, MutationLocks(chats=(old_id, new_id)))
        repository = SQLAlchemyChatRepository(session)
        group = await repository.get_by_id(old_id)
        if group is None or group.chat_type not in {"group", "supergroup"}:
            msg = "Migration source must be an active known group."
            raise ValueError(msg)
        if group.chat_id == new_id:
            return False, group.id
        if group.chat_id != old_id:
            msg = "Migration source routing is stale."
            raise ValueError(msg)
        await self._validate_target(session, new_id)
        await self._move_route(session, group, old_id, new_id)
        await session.flush()
        return True, group.id

    async def _validate_target(self, session: AsyncSession, new_id: int) -> None:
        """Reject migration targets that conflict with existing identities or handles."""
        conflict = await session.scalar(select(ChatDB.id).where(ChatDB.chat_id == new_id))
        route = await session.get(GroupRouteDB, new_id)
        if conflict is not None or route is not None:
            msg = "Migration target conflicts with an existing or deleted identity."
            raise ValueError(msg)
        handle = await session.scalar(
            select(PublicHandleDB.id).where(
                PublicHandleDB.target_kind == TargetKind.GROUP,
                PublicHandleDB.target_id == new_id,
            )
        )
        if handle is not None:
            msg = "Migration target has a conflicting public handle mapping."
            raise ValueError(msg)

    async def _move_route(self, session: AsyncSession, group: ChatDB, old_id: int, new_id: int) -> None:
        """Move chat, ban and handle routing to the new canonical ID."""
        session.add(GroupRouteDB(telegram_id=old_id, group_id=group.id))
        group.chat_id = new_id
        group.chat_type = "supergroup"
        group.bot_capabilities_verified_at = None
        await session.execute(
            update(BanDB)
            .where(
                BanDB.target_kind == TargetKind.GROUP,
                BanDB.telegram_id == old_id,
                BanDB.platform == "telegram",
            )
            .values(telegram_id=new_id)
        )
        await session.execute(
            update(PublicHandleDB)
            .where(
                PublicHandleDB.target_kind == TargetKind.GROUP,
                PublicHandleDB.target_id == old_id,
                PublicHandleDB.platform == "telegram",
            )
            .values(target_id=new_id)
        )
        state = await session.get(GroupMembershipStateDB, group.id)
        if state is not None:
            state.admins_known = False
            state.admins_expires_at = None
            state.bot_expires_at = None
            state.policy_revision += 1
