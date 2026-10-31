"""Atomic audited adapter for intentional group activation and request decisions."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.access.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.groups.activation import (
    ActivationAction,
    ActivationOutcome,
    ActivationRequest,
    ActivationResult,
    ActivationStore,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ActorOrigin, ChatStatus, TargetKind
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.activation_repository import (
    ActivationMutation,
    SQLAlchemyActivationRepository,
)
from hovorunv2.infrastructure.persistence.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository


class SQLAlchemyActivationStore(ActivationStore):
    """Commit approval, defaults, request state and actor provenance in one transaction."""

    def __init__(self, audited_uow: AuditedUnitOfWork, policy: AuthorizationPolicy) -> None:
        self._audited_uow = audited_uow
        self._policy = policy

    async def _banned(self, session: AsyncSession, kind: TargetKind, telegram_id: int) -> bool:
        """Return whether the subject has an active ban of the given kind."""
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
        """Dispatch an enable request to approval or request based on actor authority."""
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
        """Recheck authority and identity inside the serialized audited transaction."""
        await lock_async_mutation(
            session, MutationLocks(chats=(request.target.telegram_id or 0,), actors=(request.actor_id,))
        )
        repository = SQLAlchemyActivationRepository(session)
        actor = await SQLAlchemyUserRepository(session).get_by_telegram_id(request.actor_id)
        target = request.target
        chat = await SQLAlchemyChatRepository(session).get_by_id(target.telegram_id or 0)
        cli = self._is_cli_administrator(request)
        if actor is None and not cli:
            return ActivationMutation(ActivationResult(ActivationOutcome.UNAVAILABLE))
        if chat is None or not self._matches_target(chat, request):
            return ActivationMutation(ActivationResult(ActivationOutcome.UNAVAILABLE))
        if actor is not None and await self._banned(session, TargetKind.USER, actor.telegram_id):
            return ActivationMutation(ActivationResult(ActivationOutcome.DENIED))
        if chat.status == ChatStatus.BANNED or await self._banned(session, TargetKind.GROUP, chat.chat_id):
            return ActivationMutation(ActivationResult(ActivationOutcome.GROUP_BANNED))
        administrator = cli or (
            actor is not None
            and self._policy.is_allowed(PermissionFacts(actor.role, "bot:administer", PermissionTarget("bot")))
        )
        result = await self._dispatch(repository, request, chat, actor, cli=cli, administrator=administrator)
        if result.changes:
            result.changes.update(group_id=chat.id, generation=chat.generation)
            if actor is not None:
                result.changes["actor_user_id"] = actor.id
        return result

    def _is_cli_administrator(self, request: ActivationRequest) -> bool:
        """Validate the supplied administration provenance and identify CLI authority."""
        context = request.administrator_actor
        if context is not None and (
            context.origin not in {ActorOrigin.CLI, ActorOrigin.TELEGRAM}
            or (context.origin == ActorOrigin.TELEGRAM and context.actor_id != request.actor_id)
        ):
            msg = "Invalid administration actor context."
            raise PermissionError(msg)
        return context is not None and context.origin == ActorOrigin.CLI

    def _matches_target(self, chat: ChatDB, request: ActivationRequest) -> bool:
        """Bind a mutation to the current group identity and generation."""
        target = request.target
        if target.kind != "group":
            return False
        if chat.chat_id != target.telegram_id or chat.id != target.entity_id:
            return False
        if chat.generation != target.generation:
            return False
        return chat.chat_type in {"group", "supergroup"}

    async def _dispatch(
        self,
        repository: SQLAlchemyActivationRepository,
        request: ActivationRequest,
        chat: ChatDB,
        actor: UserDB | None,
        *,
        cli: bool,
        administrator: bool,
    ) -> ActivationMutation:
        """Select the authorized activation mutation for the requested action."""
        if request.action == ActivationAction.ENABLE and actor is not None:
            return await self._enable(repository, request, chat, actor, administrator=administrator)
        if request.action in {ActivationAction.GRANT, ActivationAction.APPROVE, ActivationAction.REJECT}:
            if not administrator or (not cli and request.transport_kind != "private"):
                return ActivationMutation(ActivationResult(ActivationOutcome.DENIED))
            return (
                await repository.approve(chat, actor, "administrator")
                if request.action == ActivationAction.GRANT
                else await repository.decide(request, chat, actor)
            )
        return ActivationMutation(ActivationResult(ActivationOutcome.DENIED))

    async def execute(self, request: ActivationRequest) -> ActivationResult:
        """Validate current facts inside the same transaction as all activation effects.

        Args:
            request: Activation request carrying action, target and actor provenance.

        Returns:
            Activation result produced by the audited transaction.
        """
        mutation: ActivationMutation = await self._audited_uow.execute(
            actor=request.administrator_actor or ActorContext.telegram(request.actor_id, reason="group_activation"),
            target_type="group_activation",
            target_id=request.target.entity_id or 0,
            action=request.action.value,
            operation=partial(self._execute, request=request),
            changed_fields=lambda result: result.changes,
            audit_if=lambda result: bool(result.changes),
        )
        return mutation.result
