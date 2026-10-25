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
    GroupSponsorDB,
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
        group = await self._session.get(ChatDB, group_id)
        if group is None or group.deleted_at is not None or group.chat_type not in {"group", "supergroup"}:
            msg = "An active group generation is required"
            raise ValueError(msg)

    async def _require_user(self, user_id: int) -> None:
        user = await self._session.get(UserDB, user_id)
        if user is None or user.deleted_at is not None:
            msg = "An active user generation is required"
            raise ValueError(msg)

    async def approvals(self, group_id: int) -> list[GroupApprovalDB]:
        """Fetch nondeleted/nonrevoked approvals with active parent filtering."""
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

    async def sponsors(self, group_id: int) -> list[GroupSponsorDB]:
        """Fetch active recorded sponsors, without claiming current trust/membership."""
        query = (
            select(GroupSponsorDB)
            .join(ChatDB, ChatDB.id == GroupSponsorDB.group_id)
            .join(
                UserDB,
                UserDB.id == GroupSponsorDB.user_id,
            )
            .where(
                GroupSponsorDB.group_id == group_id,
                GroupSponsorDB.deleted_at.is_(None),
                GroupSponsorDB.revoked_at.is_(None),
                ChatDB.deleted_at.is_(None),
                UserDB.deleted_at.is_(None),
            )
        )
        return list((await self._session.scalars(query)).all())

    async def requests(self, group_id: int) -> list[GroupAccessRequestDB]:
        """Fetch visible request history with active parents."""
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
        """Idempotently record explicit approval."""
        await self._require_group(group_id)
        if any(row.basis == ApprovalBasis.EXPLICIT for row in await self.approvals(group_id)):
            return GrantMutation(0)
        self._session.add(GroupApprovalDB(group_id=group_id, basis=ApprovalBasis.EXPLICIT))
        await self._session.flush()
        return GrantMutation(1, {"group_id": group_id, "basis": ApprovalBasis.EXPLICIT.value})

    async def revoke_approval(self, group_id: int, basis: ApprovalBasis) -> GrantMutation:
        """Retain revoked approval history."""
        await self._require_group(group_id)
        row = next((row for row in await self.approvals(group_id) if row.basis == basis), None)
        if row is None:
            return GrantMutation(0)
        row.revoked_at = datetime.now(UTC)
        return GrantMutation(1, {"group_id": group_id, "basis": basis.value, "revoked": True})

    async def sponsor(self, group_id: int, user_id: int) -> GrantMutation:
        """Record one sponsor without replacing existing sponsors."""
        await self._require_group(group_id)
        await self._require_user(user_id)
        if any(row.user_id == user_id for row in await self.sponsors(group_id)):
            return GrantMutation(0)
        self._session.add(GroupSponsorDB(group_id=group_id, user_id=user_id))
        await self._session.flush()
        return GrantMutation(1, {"group_id": group_id, "user_id": user_id})

    async def revoke_sponsor(self, group_id: int, user_id: int) -> GrantMutation:
        """Revoke a relationship without erasing it or other grants."""
        await self._require_group(group_id)
        row = next((row for row in await self.sponsors(group_id) if row.user_id == user_id), None)
        if row is None:
            return GrantMutation(0)
        row.revoked_at = datetime.now(UTC)
        return GrantMutation(1, {"group_id": group_id, "user_id": user_id, "revoked": True})

    async def request(self, group_id: int, user_id: int) -> GrantMutation:
        """Return a pending request, or add a new lifecycle after a prior decision."""
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
        """Decide and add approval together; rejection never changes bans/status."""
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
        """Soft-delete a visible request without touching its approval or ban state."""
        row = await self._session.get(GroupAccessRequestDB, request_id)
        if row is None or row.deleted_at is not None:
            return GrantMutation(0)
        await self._require_group(row.group_id)
        row.deleted_at = datetime.now(UTC)
        return GrantMutation(1, {"request_id": row.id, "group_id": row.group_id, "deleted": True})
