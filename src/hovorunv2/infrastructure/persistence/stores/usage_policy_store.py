"""Audited usage override transactions with current private/CLI authority checks."""

from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.features.registry import FeatureRegistry
from hovorunv2.application.usage.policies import UsagePolicy, UsagePolicyStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.enums import ActorOrigin, TargetKind
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.usage_policy_repository import (
    SQLAlchemyUsagePolicyRepository,
    UsagePolicyMutation,
)


class SQLAlchemyUsagePolicyStore(UsagePolicyStore):
    """Keep safety caps outside mutable ordinary quota records."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
        policy: AuthorizationPolicy,
        registry: FeatureRegistry,
    ) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._policy = policy
        self._registry = registry

    async def list_overrides(self, target: PermissionTarget) -> tuple[UsagePolicy, ...]:
        """Filter parent lifecycle and deleted override rows centrally."""
        async with self._session_maker() as session:
            return await SQLAlchemyUsagePolicyRepository(session).policies(target)

    async def _authorize(self, session: AsyncSession, actor: ActorContext, transport_kind: str | None) -> None:
        if actor.origin == ActorOrigin.CLI:
            return
        user = await session.scalar(
            select(UserDB).where(
                UserDB.telegram_id == actor.actor_id,
                UserDB.platform == "telegram",
                UserDB.deleted_at.is_(None),
            )
        )
        banned = await session.scalar(
            select(BanDB.id)
            .where(
                BanDB.target_kind == TargetKind.USER,
                BanDB.telegram_id == actor.actor_id,
                BanDB.platform == "telegram",
                BanDB.deleted_at.is_(None),
                BanDB.revoked_at.is_(None),
            )
            .limit(1)
        )
        msg = "Usage administration requires CLI or a current unrestricted bot administrator privately."
        if actor.origin != ActorOrigin.TELEGRAM or transport_kind != "private":
            raise PermissionError(msg)
        if user is None or banned is not None:
            raise PermissionError(msg)
        if not self._policy.is_allowed(PermissionFacts(user.role, "bot:administer", PermissionTarget("bot"))):
            raise PermissionError(msg)

    def _validate_policy(self, policy: UsagePolicy) -> None:
        scope, _, key = policy.bucket.partition(":")
        definitions = (
            (self._registry.require(key),)
            if scope == "feature"
            else tuple(definition for definition in self._registry.all() if definition.usage_policy == key)
        )
        if not definitions or not any(policy.unit in definition.units for definition in definitions):
            msg = "Unknown usage family or unsupported feature unit."
            raise ValueError(msg)

    async def _mutate(
        self,
        session: AsyncSession,
        target: PermissionTarget,
        policy: UsagePolicy,
        actor: ActorContext,
        transport_kind: str | None,
        *,
        remove: bool,
    ) -> UsagePolicyMutation:
        await lock_async_mutation(
            session,
            MutationLocks(
                user_ids=(target.entity_id or 0,) if target.kind == "user" else (),
                group_ids=(target.entity_id or 0,) if target.kind == "group" else (),
                actors=(actor.actor_id,) if actor.actor_id is not None else (),
            ),
        )
        await self._authorize(session, actor, transport_kind)
        return await SQLAlchemyUsagePolicyRepository(session).set_policy(target, policy, remove=remove)

    async def set_override(
        self,
        target: PermissionTarget,
        policy: UsagePolicy,
        *,
        actor: ActorContext,
        transport_kind: str | None = None,
        remove: bool = False,
    ) -> bool:
        """Commit one mutation and explicit before/after audit atomically."""
        self._validate_policy(policy)
        result: UsagePolicyMutation = await self._audited_uow.execute(
            actor=actor,
            target_type="usage_override",
            target_id=f"{target.kind}:{target.entity_id}",
            action="remove" if remove else "set",
            operation=partial(
                self._mutate, target=target, policy=policy, actor=actor, transport_kind=transport_kind, remove=remove
            ),
            changed_fields=lambda result: result.changes,
            audit_if=lambda result: result.changed,
        )
        return result.changed
