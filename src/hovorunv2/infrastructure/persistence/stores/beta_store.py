"""Atomic, current-authority beta entitlement mutations and lifecycle-filtered reads."""

from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.features.beta import BetaEligibilityFacts, BetaEntitlementStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.beta import ALL_BETA
from hovorunv2.domain.enums import ActorOrigin
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.beta_repository import SQLAlchemyBetaEntitlementRepository


class SQLAlchemyBetaEntitlementStore(BetaEntitlementStore):
    """Keep beta entitlements bound to internal identities without changing selections."""

    def __init__(
        self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork, policy: AuthorizationPolicy
    ) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._policy = policy

    async def facts(self, target: PermissionTarget, feature: str) -> BetaEligibilityFacts:
        """Read every effective grant fresh; revocation requires no cache invalidation."""
        async with self._session_maker() as session:
            return await SQLAlchemyBetaEntitlementRepository(session).facts(target, feature)

    async def _authorize(self, session: AsyncSession, actor: ActorContext, transport_kind: str | None) -> None:
        if actor.origin == ActorOrigin.CLI:
            return
        user = await session.scalar(
            select(UserDB).where(
                UserDB.telegram_id == actor.actor_id, UserDB.platform == "telegram", UserDB.deleted_at.is_(None)
            )
        )
        if (
            actor.origin != ActorOrigin.TELEGRAM
            or transport_kind != "private"
            or user is None
            or not self._policy.is_allowed(PermissionFacts(user.role, "bot:administer", PermissionTarget("bot")))
        ):
            msg = "Beta administration requires CLI or current bot administrator in private transport."
            raise PermissionError(msg)

    async def _mutate(
        self,
        session: AsyncSession,
        target: PermissionTarget,
        feature: str,
        *,
        granted: bool,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> bool:
        # The audited transaction serializes current authority and lifecycle reads.
        await lock_async_mutation(
            session,
            MutationLocks(
                user_ids=(target.entity_id or 0,) if target.kind == "user" else (),
                group_ids=(target.entity_id or 0,) if target.kind == "group" else (),
                actors=(actor.actor_id,) if actor.actor_id is not None else (),
            ),
        )
        await self._authorize(session, actor, transport_kind)
        repository = SQLAlchemyBetaEntitlementRepository(session)
        entity = await repository.target(target)
        if entity is None:
            msg = "Beta target must match a known active identity and generation."
            raise ValueError(msg)
        return await repository.set_grant(entity, feature, granted=granted)

    async def set_grant(
        self,
        target: PermissionTarget,
        feature: str,
        *,
        granted: bool,
        actor: ActorContext,
        transport_kind: str | None = None,
    ) -> bool:
        """Persist an entitlement and minimal audit event in one transaction."""
        if target.kind not in {"user", "group"} or not feature or (target.kind == "group" and feature == ALL_BETA):
            msg = "Invalid beta entitlement subject or feature."
            raise ValueError(msg)
        return await self._audited_uow.execute(
            actor=actor,
            target_type="beta_entitlement",
            target_id=f"{target.kind}:{target.entity_id}:{target.generation}",
            action="grant" if granted else "revoke",
            operation=partial(
                self._mutate,
                target=target,
                feature=feature,
                granted=granted,
                actor=actor,
                transport_kind=transport_kind,
            ),
            changed_fields={"feature": feature, "granted": [not granted, granted], "generation": target.generation},
            audit_if=bool,
        )
