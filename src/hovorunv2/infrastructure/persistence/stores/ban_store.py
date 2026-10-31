"""Transactional independent ban storage with current authority checks."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB, BanKind
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ActorOrigin, Role, TargetKind
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation


class SQLAlchemyBanStore(BanStore):
    """Keep restrictions independent of presence, legacy status and access grants."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def _target(self, session: AsyncSession, target: TargetKind, telegram_id: int) -> UserDB | ChatDB | None:
        if target == TargetKind.USER:
            return await session.scalar(
                select(UserDB).where(
                    UserDB.telegram_id == telegram_id,
                    UserDB.platform == "telegram",
                    UserDB.deleted_at.is_(None),
                )
            )
        return await session.scalar(
            select(ChatDB).where(
                ChatDB.chat_id == telegram_id,
                ChatDB.platform == "telegram",
                ChatDB.deleted_at.is_(None),
                ChatDB.chat_type.in_(("group", "supergroup")),
            )
        )

    async def list_active(self, target: TargetKind, telegram_id: int) -> list[BanDB]:
        """Filter tombstoned subjects and restriction rows for ordinary reads."""
        async with self._session_maker() as session:
            if await self._target(session, target, telegram_id) is None:
                return []
            return list(
                (
                    await session.scalars(
                        select(BanDB).where(
                            BanDB.target_kind == target,
                            BanDB.telegram_id == telegram_id,
                            BanDB.platform == "telegram",
                            BanDB.revoked_at.is_(None),
                            BanDB.deleted_at.is_(None),
                        )
                    )
                ).all()
            )

    async def policy_revision(self, target: TargetKind, telegram_id: int) -> int:
        """Include all history so revocation/reapplication changes the subject revision."""
        async with self._session_maker() as session:
            return int(
                await session.scalar(
                    select(func.coalesce(func.sum(BanDB.revision), 0)).where(
                        BanDB.target_kind == target,
                        BanDB.telegram_id == telegram_id,
                        BanDB.platform == "telegram",
                    )
                )
                or 0
            )

    async def _mutate(
        self,
        session: AsyncSession,
        target: TargetKind,
        telegram_id: int,
        kind: BanKind,
        *,
        active: bool,
        actor: ActorContext,
        public_explanation: str | None,
    ) -> bool:
        await lock_async_mutation(
            session,
            MutationLocks(
                users=(telegram_id,) if target == TargetKind.USER else (),
                chats=(telegram_id,) if target == TargetKind.GROUP else (),
                actors=(actor.actor_id,) if actor.actor_id is not None else (),
            ),
        )
        entity = await self._target(session, target, telegram_id)
        if entity is None:
            msg = "Ban target must be an active known identity."
            raise ValueError(msg)
        await self._validate_authority(session, actor, entity)
        ban = await session.scalar(
            select(BanDB).where(
                BanDB.target_kind == target,
                BanDB.telegram_id == telegram_id,
                BanDB.platform == "telegram",
                BanDB.kind == kind,
                BanDB.revoked_at.is_(None),
                BanDB.deleted_at.is_(None),
            )
        )
        changed = self._apply_ban_change(
            session, ban, target, telegram_id, kind, active=active, actor=actor, public_explanation=public_explanation
        )
        if not changed:
            return False
        await session.flush()
        return True

    def _apply_ban_change(
        self,
        session: AsyncSession,
        ban: BanDB | None,
        target: TargetKind,
        telegram_id: int,
        kind: BanKind,
        *,
        active: bool,
        actor: ActorContext,
        public_explanation: str | None,
    ) -> bool:
        """Apply or revoke the active restriction; report an idempotent no-op."""
        if active:
            if ban is not None:
                return False
            session.add(
                BanDB(
                    target_kind=target,
                    telegram_id=telegram_id,
                    kind=kind,
                    internal_reason=actor.reason or "",
                    public_explanation=public_explanation,
                )
            )
            return True
        if ban is None:
            return False
        ban.revoked_at = datetime.now(UTC)
        ban.revision += 1
        return True

    async def _validate_authority(self, session: AsyncSession, actor: ActorContext, entity: UserDB | ChatDB) -> None:
        """Check current operator and protected target authority inside the locked transaction."""
        if actor.origin == ActorOrigin.TELEGRAM:
            operator = await self._target(session, TargetKind.USER, actor.actor_id or 0)
            if not isinstance(operator, UserDB) or operator.role < Role.ADMIN:
                msg = "Ban administration requires current bot administrator authority."
                raise PermissionError(msg)
            if isinstance(entity, UserDB) and entity.role >= Role.ADMIN:
                msg = f"Protected {entity.role.name.title()} restrictions can only be changed through CLI."
                raise PermissionError(msg)
        elif actor.origin != ActorOrigin.CLI:
            msg = "Ban administration requires CLI or Telegram administrator origin."
            raise PermissionError(msg)

    async def set_ban(
        self,
        target: TargetKind,
        telegram_id: int,
        kind: BanKind,
        *,
        active: bool,
        actor: ActorContext,
        public_explanation: str | None = None,
    ) -> bool:
        """Validate and atomically mutate restriction plus privacy-limited audit event."""
        if not actor.reason or not actor.reason.strip():
            msg = "Ban administration requires an internal reason."
            raise ValueError(msg)
        if target == TargetKind.GROUP and kind != BanKind.FULL:
            msg = "Groups support only full bans."
            raise ValueError(msg)
        return await self._audited_uow.execute(
            actor=actor,
            target_type="ban",
            target_id=f"{target}:{telegram_id}:{kind}",
            action="apply" if active else "revoke",
            operation=partial(
                self._mutate,
                target=target,
                telegram_id=telegram_id,
                kind=kind,
                active=active,
                actor=actor,
                public_explanation=public_explanation,
            ),
            changed_fields={
                "active": [not active, active],
                "kind": kind.value,
                "public_explanation_changed": public_explanation is not None,
            },
            audit_if=bool,
        )
