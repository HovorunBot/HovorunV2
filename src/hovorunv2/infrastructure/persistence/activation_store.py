"""Atomic audited adapter for intentional group activation and request decisions."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.activation import (
    ActivationAction,
    ActivationOutcome,
    ActivationRequest,
    ActivationResult,
    ActivationStore,
)
from hovorunv2.application.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.ports import AuditedUnitOfWork, AuthorizationPolicy
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.activation_repository import (
    ActivationMutation,
    SQLAlchemyActivationRepository,
)
from hovorunv2.infrastructure.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


class SQLAlchemyActivationStore(ActivationStore):
    """Commit approval, defaults, request state and actor provenance in one transaction."""

    def __init__(self, audited_uow: AuditedUnitOfWork, policy: AuthorizationPolicy) -> None:
        self._audited_uow = audited_uow
        self._policy = policy

    async def _banned(self, session: AsyncSession, kind: TargetKind, telegram_id: int) -> bool:
        return (
            await session.scalar(
                select(BanDB.id)
                .where(
                    BanDB.target_kind == kind,
                    BanDB.telegram_id == telegram_id,
                    BanDB.platform == "telegram",
                    BanDB.deleted_at.is_(None),
                    BanDB.revoked_at.is_(None),
                )
                .limit(1)
            )
            is not None
        )

    async def _enable(
        self,
        repository: SQLAlchemyActivationRepository,
        request: ActivationRequest,
        chat: ChatDB,
        actor: UserDB,
        *,
        administrator: bool,
    ) -> ActivationMutation:
        if request.transport_kind not in {"group", "supergroup"}:
            return ActivationMutation(ActivationResult(ActivationOutcome.DENIED))
        if administrator or actor.is_trusted:
            return await repository.approve(chat, actor, "administrator" if administrator else "trust")
        if request.group_admin is not None and request.group_admin.applies_to(
            actor.telegram_id, request.target, datetime.now(UTC)
        ):
            return await repository.request(chat, actor)
        return ActivationMutation(ActivationResult(ActivationOutcome.DENIED))

    async def _execute(self, session: AsyncSession, request: ActivationRequest) -> ActivationMutation:
        """Serialize SQLite writes before reading authority, bans, identity or idempotency state."""
        await session.execute(text("BEGIN IMMEDIATE"))
        repository = SQLAlchemyActivationRepository(session)
        actor = await SQLAlchemyUserRepository(session).get_by_telegram_id(request.actor_id)
        target = request.target
        chat = await SQLAlchemyChatRepository(session).get_by_id(target.telegram_id or 0)
        if (
            actor is None
            or chat is None
            or target.kind != "group"
            or chat.id != target.entity_id
            or chat.generation != target.generation
            or chat.chat_type not in {"group", "supergroup"}
        ):
            return ActivationMutation(ActivationResult(ActivationOutcome.UNAVAILABLE))
        if await self._banned(session, TargetKind.USER, actor.telegram_id):
            return ActivationMutation(ActivationResult(ActivationOutcome.DENIED))
        if chat.status == ChatStatus.BANNED or await self._banned(session, TargetKind.GROUP, chat.chat_id):
            return ActivationMutation(ActivationResult(ActivationOutcome.GROUP_BANNED))
        administrator = self._policy.is_allowed(PermissionFacts(actor.role, "bot:administer", PermissionTarget("bot")))
        if request.action == ActivationAction.ENABLE:
            result = await self._enable(repository, request, chat, actor, administrator=administrator)
        elif request.action in {ActivationAction.APPROVE, ActivationAction.REJECT}:
            if not administrator or request.transport_kind != "private":
                return ActivationMutation(ActivationResult(ActivationOutcome.DENIED))
            result = await repository.decide(request, chat, actor)
        else:
            return ActivationMutation(ActivationResult(ActivationOutcome.DENIED))
        if result.changes:
            result.changes.update(group_id=chat.id, generation=chat.generation, actor_user_id=actor.id)
        return result

    async def execute(self, request: ActivationRequest) -> ActivationResult:
        """Validate current facts inside the same transaction as all activation effects."""
        mutation: ActivationMutation = await self._audited_uow.execute(
            actor=ActorContext.telegram(request.actor_id, reason="group_activation"),
            target_type="group_activation",
            target_id=request.target.entity_id or 0,
            action=request.action.value,
            operation=partial(self._execute, request=request),
            changed_fields=lambda result: result.changes,
            audit_if=lambda result: bool(result.changes),
        )
        return mutation.result
