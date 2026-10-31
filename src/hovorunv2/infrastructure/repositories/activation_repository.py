"""Session-bound activation checks and composite group mutations."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.groups.activation import (
    ActivationAction,
    ActivationOutcome,
    ActivationRequest,
    ActivationResult,
)
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, ChatStatus
from hovorunv2.domain.group_grant import GroupAccessRequestDB, GroupApprovalDB, RequestStatus
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.group_grant_repository import SQLAlchemyGroupGrantRepository


@dataclass(frozen=True, slots=True)
class ActivationMutation:
    """Pair a safe result with explicit audit fields covering the entire transaction."""

    result: ActivationResult
    changes: dict[str, object] = field(default_factory=dict)


class SQLAlchemyActivationRepository:
    """Persist durable approval without a continuing relationship to its activating user."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._grants = SQLAlchemyGroupGrantRepository(session)

    async def _approved(self, chat: ChatDB) -> bool:
        return bool(await self._grants.approvals(chat.id)) or (
            chat.status == ChatStatus.APPROVED and not await self._grants.has_revoked_approval(chat.id)
        )

    async def _pending(self, group_id: int) -> GroupAccessRequestDB | None:
        return await self._session.scalar(
            select(GroupAccessRequestDB).where(
                GroupAccessRequestDB.group_id == group_id,
                GroupAccessRequestDB.deleted_at.is_(None),
                GroupAccessRequestDB.status == RequestStatus.PENDING,
            )
        )

    async def _add_defaults(self, chat: ChatDB) -> list[str]:
        history = await self._session.scalar(
            select(GroupApprovalDB.id).where(GroupApprovalDB.group_id == chat.id).limit(1)
        )
        if history is not None or chat.features_configured or chat.commands or chat.legacy_empty_capture:
            return []
        names = {str(command) for commands in PROVIDER_SPLIT_COMMANDS.values() for command in commands}
        commands = list((await self._session.scalars(select(CommandDB).where(CommandDB.name.in_(names)))).all())
        if len(commands) != len(names):
            msg = "Default media command definitions are incomplete"
            raise ValueError(msg)
        chat.commands.extend(commands)
        chat.features_configured = True
        return sorted(names)

    def _close_request(self, pending: GroupAccessRequestDB, status: RequestStatus, actor_id: int | None) -> None:
        pending.status = status
        pending.decided_at = datetime.now(UTC)
        pending.decision_actor_id = actor_id
        pending.decision_reason = "group_activation"

    async def approve(self, chat: ChatDB, actor: UserDB | None, basis: str) -> ActivationMutation:
        """Persist approval, first-activation defaults and any pending request decision together."""
        approved = await self._approved(chat)
        pending = await self._pending(chat.id)
        if approved and pending is None:
            return ActivationMutation(ActivationResult(ActivationOutcome.ALREADY_APPROVED))
        old_status = chat.status
        defaults = [] if approved else await self._add_defaults(chat)
        approval = None if approved else await self._grants.approve(chat.id)
        chat.status = ChatStatus.APPROVED
        changes: dict[str, object] = {
            "activation_basis": basis,
            "approval_added": bool(approval and approval.result),
            "defaults_added": defaults,
            "status": [old_status.value, chat.status.value],
        }
        if pending is not None:
            self._close_request(pending, RequestStatus.APPROVED, actor.telegram_id if actor is not None else None)
            changes.update(
                request_id=pending.id, request_status=[RequestStatus.PENDING.value, RequestStatus.APPROVED.value]
            )
        return ActivationMutation(ActivationResult(ActivationOutcome.APPROVED), changes)

    async def request(self, chat: ChatDB, actor: UserDB) -> ActivationMutation:
        """Create one pending request for an inactive group without changing existing selections."""
        if await self._approved(chat):
            return ActivationMutation(ActivationResult(ActivationOutcome.ALREADY_APPROVED))
        mutation = await self._grants.request(chat.id, actor.id)
        if not mutation.changes:
            return ActivationMutation(ActivationResult(ActivationOutcome.ALREADY_PENDING, mutation.result))
        old_status = chat.status
        chat.status = ChatStatus.PENDING
        return ActivationMutation(
            ActivationResult(ActivationOutcome.REQUESTED, mutation.result),
            {
                "request_id": mutation.result,
                "request_status": [None, RequestStatus.PENDING.value],
                "status": [old_status.value, chat.status.value],
                "activation_basis": "request",
            },
        )

    async def decide(self, request: ActivationRequest, chat: ChatDB, actor: UserDB | None) -> ActivationMutation:
        """Persist a decision only for the selected active pending request and requester."""
        pending = await self._pending(chat.id)
        if pending is None or pending.id != request.request_id:
            return ActivationMutation(ActivationResult(ActivationOutcome.STALE_REQUEST))
        requester = await self._session.get(UserDB, pending.requested_by_user_id)
        if requester is None or requester.deleted_at is not None:
            return ActivationMutation(ActivationResult(ActivationOutcome.STALE_REQUEST))
        if request.action == ActivationAction.APPROVE:
            return await self.approve(chat, actor, "administrator")
        old_status = chat.status
        if not await self._approved(chat):
            chat.status = ChatStatus.REJECTED
        self._close_request(pending, RequestStatus.REJECTED, actor.telegram_id if actor is not None else None)
        return ActivationMutation(
            ActivationResult(ActivationOutcome.REJECTED, pending.id),
            {
                "request_id": pending.id,
                "request_status": [RequestStatus.PENDING.value, RequestStatus.REJECTED.value],
                "status": [old_status.value, chat.status.value],
                "activation_basis": "administrator",
            },
        )
