"""Atomic audited adapter for durable group grants and request decisions."""

from collections.abc import Awaitable, Callable
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.ports import AuditedUnitOfWork
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.group_grant import (
    ApprovalBasis,
    GroupAccessRequestDB,
    GroupApprovalDB,
    GroupSponsorDB,
    RequestStatus,
)
from hovorunv2.infrastructure.repositories.group_grant_repository import GrantMutation, SQLAlchemyGroupGrantRepository


class SQLAlchemyGroupGrantStore(GroupGrantStore):
    """Persist bases independently from effective runtime access and ChatStatus."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        """Receive the mandatory transaction and session boundaries."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def approvals(self, group_id: int) -> list[GroupApprovalDB]:
        """List visible active approvals."""
        async with self._session_maker() as session:
            return await SQLAlchemyGroupGrantRepository(session).approvals(group_id)

    async def sponsors(self, group_id: int) -> list[GroupSponsorDB]:
        """List recorded sponsors; current validity remains computed elsewhere."""
        async with self._session_maker() as session:
            return await SQLAlchemyGroupGrantRepository(session).sponsors(group_id)

    async def requests(self, group_id: int) -> list[GroupAccessRequestDB]:
        """List visible request lifecycle records."""
        async with self._session_maker() as session:
            return await SQLAlchemyGroupGrantRepository(session).requests(group_id)

    async def _execute(
        self,
        session: AsyncSession,
        operation: Callable[[SQLAlchemyGroupGrantRepository], Awaitable[GrantMutation]],
    ) -> GrantMutation:
        return await operation(SQLAlchemyGroupGrantRepository(session))

    async def _mutate(
        self,
        target_id: int,
        action: str,
        actor: ActorContext,
        operation: Callable[[SQLAlchemyGroupGrantRepository], Awaitable[GrantMutation]],
    ) -> int:
        result: GrantMutation = await self._audited_uow.execute(
            actor=actor,
            target_type="group_grant",
            target_id=target_id,
            action=action,
            operation=partial(self._execute, operation=operation),
            changed_fields=lambda result: result.changes,
            audit_if=lambda result: bool(result.changes),
        )
        return result.result

    async def approve(self, group_id: int, actor: ActorContext) -> bool:
        """Idempotently grant independent explicit approval."""
        return bool(
            await self._mutate(
                group_id, "approve", actor, partial(SQLAlchemyGroupGrantRepository.approve, group_id=group_id)
            )
        )

    async def revoke_approval(self, group_id: int, basis: ApprovalBasis, actor: ActorContext) -> bool:
        """Revoke approval without destroying history."""
        return bool(
            await self._mutate(
                group_id,
                "revoke_approval",
                actor,
                partial(SQLAlchemyGroupGrantRepository.revoke_approval, group_id=group_id, basis=basis),
            )
        )

    async def sponsor(self, group_id: int, user_id: int, actor: ActorContext) -> bool:
        """Idempotently add a sponsor relationship."""
        return bool(
            await self._mutate(
                group_id,
                "sponsor",
                actor,
                partial(SQLAlchemyGroupGrantRepository.sponsor, group_id=group_id, user_id=user_id),
            )
        )

    async def revoke_sponsor(self, group_id: int, user_id: int, actor: ActorContext) -> bool:
        """Revoke one relationship without touching other bases."""
        return bool(
            await self._mutate(
                group_id,
                "revoke_sponsor",
                actor,
                partial(SQLAlchemyGroupGrantRepository.revoke_sponsor, group_id=group_id, user_id=user_id),
            )
        )

    async def request(self, group_id: int, user_id: int, actor: ActorContext) -> int:
        """Create or return a pending request without changing runtime state."""
        return await self._mutate(
            group_id,
            "request",
            actor,
            partial(SQLAlchemyGroupGrantRepository.request, group_id=group_id, user_id=user_id),
        )

    async def decide(self, request_id: int, status: RequestStatus, actor: ActorContext) -> bool:
        """Commit the decision and optional approval in one audited transaction."""
        return bool(
            await self._mutate(
                request_id,
                "decide_request",
                actor,
                partial(
                    SQLAlchemyGroupGrantRepository.decide,
                    request_id=request_id,
                    status=status,
                    actor_id=actor.actor_id,
                    reason=actor.reason,
                ),
            )
        )

    async def delete_request(self, request_id: int, actor: ActorContext) -> bool:
        """Soft-delete a request preserving decisions and access bases."""
        return bool(
            await self._mutate(
                request_id,
                "delete_request",
                actor,
                partial(SQLAlchemyGroupGrantRepository.delete_request, request_id=request_id),
            )
        )
