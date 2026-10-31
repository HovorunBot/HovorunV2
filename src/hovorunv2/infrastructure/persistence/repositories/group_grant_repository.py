"""Session-bound group grants; transactions and audit belong to the store."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.group_grant import (
    ApprovalBasis,
    GroupAccessRequestDB,
    GroupApprovalDB,
    RequestStatus,
)
from hovorunv2.domain.user import UserDB


@dataclass(frozen=True, slots=True)
class GrantMutation:
    """Return value plus explicitly enumerated operational audit fields."""

    result: int
    changes: dict[str, object] = field(default_factory=dict)


class SQLAlchemyGroupGrantRepository:
    """Persist generation-bound access bases without altering runtime chat status."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's transaction."""
        self._session = session

    async def _require_group(self, group_id: int) -> None:
        """Require an active, nondeleted group generation."""
        group = await self._session.get(ChatDB, group_id)
        if group is None or group.deleted_at is not None or group.chat_type not in {"group", "supergroup"}:
            msg = "An active group generation is required"
            raise ValueError(msg)

    async def _require_user(self, user_id: int) -> None:
        """Require an active, nondeleted user generation."""
        user = await self._session.get(UserDB, user_id)
        if user is None or user.deleted_at is not None:
            msg = "An active user generation is required"
            raise ValueError(msg)

    async def approvals(self, group_id: int) -> list[GroupApprovalDB]:
        """Fetch nondeleted/nonrevoked approvals with active parent filtering.

        Args:
            group_id: Internal group entity ID.

        Returns:
            Visible active approvals for the group.
        """
        query = (
            select(GroupApprovalDB)
            .join(ChatDB, ChatDB.id == GroupApprovalDB.group_id)
            .where(
                GroupApprovalDB.group_id == group_id,
                GroupApprovalDB.deleted_at.is_(None),
                GroupApprovalDB.revoked_at.is_(None),
                ChatDB.deleted_at.is_(None),
            )
        )
        return list((await self._session.scalars(query)).all())

    async def has_revoked_approval(self, group_id: int) -> bool:
        """Check whether any approval for this group was explicitly revoked.

        Args:
            group_id: Internal group entity ID.

        Returns:
            True if the group has any explicitly revoked approval.
        """
        query = (
            select(GroupApprovalDB.id)
            .join(ChatDB, ChatDB.id == GroupApprovalDB.group_id)
            .where(
                GroupApprovalDB.group_id == group_id,
                GroupApprovalDB.deleted_at.is_(None),
                GroupApprovalDB.revoked_at.is_not(None),
                ChatDB.deleted_at.is_(None),
            )
            .limit(1)
        )
        return (await self._session.scalar(query)) is not None

    async def requests(self, group_id: int) -> list[GroupAccessRequestDB]:
        """Fetch visible request history with active parents.

        Args:
            group_id: Internal group entity ID.

        Returns:
            Visible request lifecycle records for the group.
        """
        query = (
            select(GroupAccessRequestDB)
            .join(ChatDB, ChatDB.id == GroupAccessRequestDB.group_id)
            .join(
                UserDB,
                UserDB.id == GroupAccessRequestDB.requested_by_user_id,
            )
            .where(
                GroupAccessRequestDB.group_id == group_id,
                GroupAccessRequestDB.deleted_at.is_(None),
                ChatDB.deleted_at.is_(None),
                UserDB.deleted_at.is_(None),
            )
        )
        return list((await self._session.scalars(query)).all())

    async def approve(self, group_id: int) -> GrantMutation:
        """Idempotently record explicit approval.

        Args:
            group_id: Internal group entity ID.

        Returns:
            Mutation carrying the approval result and audit fields.
        """
        await self._require_group(group_id)
        if any(row.basis == ApprovalBasis.EXPLICIT for row in await self.approvals(group_id)):
            return GrantMutation(0)
        self._session.add(GroupApprovalDB(group_id=group_id, basis=ApprovalBasis.EXPLICIT))
        await self._session.flush()
        return GrantMutation(1, {"group_id": group_id, "basis": ApprovalBasis.EXPLICIT.value})

    async def revoke_approval(self, group_id: int, basis: ApprovalBasis) -> GrantMutation:
        """Retain revoked approval history.

        Args:
            group_id: Internal group entity ID.
            basis: Approval basis to revoke.

        Returns:
            Mutation carrying the revocation result and audit fields.
        """
        await self._require_group(group_id)
        row = next((row for row in await self.approvals(group_id) if row.basis == basis), None)
        if row is None:
            return GrantMutation(0)
        row.revoked_at = datetime.now(UTC)
        return GrantMutation(1, {"group_id": group_id, "basis": basis.value, "revoked": True})

    async def request(self, group_id: int, user_id: int) -> GrantMutation:
        """Return a pending request, or add a new lifecycle after a prior decision.

        Args:
            group_id: Internal group entity ID.
            user_id: Internal user entity ID of the requester.

        Returns:
            Mutation carrying the request ID and audit fields.
        """
        await self._require_group(group_id)
        await self._require_user(user_id)
        # A deleted requester must not hide the group's unique pending request.
        query = select(GroupAccessRequestDB).where(
            GroupAccessRequestDB.group_id == group_id,
            GroupAccessRequestDB.deleted_at.is_(None),
            GroupAccessRequestDB.status == RequestStatus.PENDING,
        )
        existing = await self._session.scalar(query)
        if existing is not None:
            return GrantMutation(existing.id)
        row = GroupAccessRequestDB(group_id=group_id, requested_by_user_id=user_id)
        self._session.add(row)
        await self._session.flush()
        return GrantMutation(
            row.id, {"group_id": group_id, "requested_by_user_id": user_id, "status": RequestStatus.PENDING.value}
        )

    async def decide(
        self, request_id: int, status: RequestStatus, actor_id: int | None, reason: str | None
    ) -> GrantMutation:
        """Decide and add approval together; rejection never changes bans/status.

        Args:
            request_id: Internal ID of the pending request.
            status: Terminal decision status.
            actor_id: ID of the deciding actor, or None.
            reason: Decision reason recorded on the request.

        Returns:
            Mutation carrying the decision result and audit fields.

        Raises:
            ValueError: If the requested status is still pending.
        """
        if status == RequestStatus.PENDING:
            msg = "A decision must terminate a pending request"
            raise ValueError(msg)
        row = await self._session.get(GroupAccessRequestDB, request_id)
        if row is None or row.deleted_at is not None:
            return GrantMutation(0)
        await self._require_group(row.group_id)
        await self._require_user(row.requested_by_user_id)
        if row.status != RequestStatus.PENDING:
            return GrantMutation(0)
        approval = await self.approve(row.group_id) if status == RequestStatus.APPROVED else GrantMutation(0)
        row.status = status
        row.decided_at = datetime.now(UTC)
        row.decision_actor_id = actor_id
        row.decision_reason = reason
        return GrantMutation(
            1,
            {
                "request_id": row.id,
                "group_id": row.group_id,
                "status": [RequestStatus.PENDING.value, status.value],
                "explicit_approval_added": bool(approval.result),
            },
        )

    async def delete_request(self, request_id: int) -> GrantMutation:
        """Soft-delete a visible request without touching its approval or ban state.

        Args:
            request_id: Internal ID of the request.

        Returns:
            Mutation carrying the deletion result and audit fields.
        """
        row = await self._session.get(GroupAccessRequestDB, request_id)
        if row is None or row.deleted_at is not None:
            return GrantMutation(0)
        await self._require_group(row.group_id)
        row.deleted_at = datetime.now(UTC)
        return GrantMutation(1, {"request_id": row.id, "group_id": row.group_id, "deleted": True})
