"""Audited whole-group beta activation with current transactional authority checks."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.features.group_beta import GroupBetaFacts, GroupBetaStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.beta import ALL_BETA
from hovorunv2.domain.enums import ActorOrigin, ChatStatus, TargetKind
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.beta_repository import SQLAlchemyBetaEntitlementRepository
from hovorunv2.infrastructure.persistence.repositories.group_beta_repository import (
    GroupBetaMutation,
    SQLAlchemyGroupBetaRepository,
)
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository


class SQLAlchemyGroupBetaStore(GroupBetaStore):
    """Persist selected state without copying grants or granting unrelated group approval."""

    def __init__(
        self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork, policy: AuthorizationPolicy
    ) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._policy = policy

    async def facts(self, target: PermissionTarget, feature: str, now: datetime) -> GroupBetaFacts:
        """Read effective support from current database state; no ordinary-member identity is needed.

        Args:
            target: Permission target identifying the group and its numeric route.
            feature: Feature key being evaluated.
            now: Evaluation timestamp for freshness bounds.

        Returns:
            Group beta facts reflecting selection, grants and qualifying support.

        Raises:
            ValueError: If the evaluation timestamp is naive.
        """
        if now.utcoffset() is None:
            msg = "Beta evaluation requires an aware timestamp."
            raise ValueError(msg)
        async with self._session_maker() as session:
            return await SQLAlchemyGroupBetaRepository(session).facts(target, feature, now)

    async def _banned(self, session: AsyncSession, kind: TargetKind, telegram_id: int) -> bool:
        """Return whether the subject has an active ban of the given kind."""
        return (
            await session.scalar(
                select(BanDB.id)
                .where(
                    BanDB.target_kind == kind,
                    BanDB.telegram_id == telegram_id,
                    BanDB.platform == "telegram",
                    BanDB.revoked_at.is_(None),
                    BanDB.deleted_at.is_(None),
                )
                .limit(1)
            )
            is not None
        )

    async def _actor(
        self,
        session: AsyncSession,
        repository: SQLAlchemyGroupBetaRepository,
        target: PermissionTarget,
        actor: ActorContext,
        transport_kind: str | None,
        now: datetime,
    ) -> tuple[UserDB | None, bool, bool]:
        """Authorize the acting human administrator and return their identity and flags."""
        if actor.origin == ActorOrigin.CLI:
            return None, True, False
        user = await SQLAlchemyUserRepository(session).get_by_telegram_id(actor.actor_id or 0)
        if actor.origin != ActorOrigin.TELEGRAM or user is None:
            msg = "Group beta changes require a current human administrator or CLI."
            raise PermissionError(msg)
        if await self._banned(session, TargetKind.USER, user.telegram_id):
            msg = "Restricted actors cannot change group beta activation."
            raise PermissionError(msg)
        bot_admin = self._policy.is_allowed(PermissionFacts(user.role, "bot:administer", PermissionTarget("bot")))
        local_admin = await repository.is_admin(target.entity_id or 0, user.id, now)
        if transport_kind not in {"private", "group", "supergroup"} or not self._policy.is_allowed(
            PermissionFacts(user.role, "group:settings", target, local_admin)
        ):
            msg = "Group beta changes require current settings authority in the selected transport."
            raise PermissionError(msg)
        return user, bot_admin, local_admin

    async def _mutate(
        self,
        session: AsyncSession,
        target: PermissionTarget,
        feature: str,
        *,
        enabled: bool,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> GroupBetaMutation:
        """Apply one selection change inside the locked, audited transaction."""
        await lock_async_mutation(
            session,
            MutationLocks(
                group_ids=(target.entity_id or 0,), actors=(actor.actor_id,) if actor.actor_id is not None else ()
            ),
        )
        now = datetime.now(UTC)
        group = await SQLAlchemyBetaEntitlementRepository(session).target(target)
        if group is None:
            msg = "Group beta target must match a known active group identity and generation."
            raise ValueError(msg)
        if isinstance(group, UserDB):
            msg = "Group beta activation requires a group identity."
            raise TypeError(msg)
        if group.status == ChatStatus.BANNED or await self._banned(session, TargetKind.GROUP, group.chat_id):
            msg = "Restricted groups cannot change beta activation."
            raise PermissionError(msg)
        repository = SQLAlchemyGroupBetaRepository(session)
        user, bot_admin, local_admin = await self._actor(session, repository, target, actor, transport_kind, now)
        enabler = None
        if enabled:
            local_support = (
                user is not None
                and local_admin
                and transport_kind in {"private", "group", "supergroup"}
                and await repository.user_eligible(user.id, feature)
            )
            if local_support:
                enabler = user
            elif not bot_admin or not await repository.group_granted(group.id, feature):
                msg = "Group beta activation requires eligible local support or an explicit group grant."
                raise PermissionError(msg)
        return await repository.set_enabled(group, feature, enabled=enabled, enabler=enabler, now=now)

    async def set_enabled(
        self,
        target: PermissionTarget,
        feature: str,
        *,
        enabled: bool,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> bool:
        """Mutate selection and supporting intent with one minimal business audit event.

        Args:
            target: Permission target identifying the group.
            feature: Feature key of the selection.
            enabled: Whether the feature is selected.
            actor: Actor context recorded on the audit event.
            transport_kind: Ingress transport used for settings authorization.

        Returns:
            True if the selection changed.

        Raises:
            ValueError: If the target is not a group, the feature is empty, or it is the all-beta wildcard.
        """
        if target.kind != "group" or not feature or feature == ALL_BETA:
            msg = "Group beta activation requires one group and one specific feature."
            raise ValueError(msg)
        mutation: GroupBetaMutation = await self._audited_uow.execute(
            actor=actor,
            target_type="group_beta",
            target_id=target.entity_id or 0,
            action="enable" if enabled else "disable",
            operation=partial(
                self._mutate,
                target=target,
                feature=feature,
                enabled=enabled,
                actor=actor,
                transport_kind=transport_kind,
            ),
            changed_fields=lambda result: result.changes,
            audit_if=lambda result: result.changed,
        )
        return mutation.changed
