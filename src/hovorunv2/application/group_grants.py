"""Application boundary for durable group access bases and independent requests."""

from typing import Protocol

from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.group_grant import (
    ApprovalBasis,
    GroupAccessRequestDB,
    GroupApprovalDB,
    RequestStatus,
)


class GroupGrantStore(Protocol):
    """Audited composite commands; live activation and permission checks belong to their workflows."""

    async def approvals(self, group_id: int) -> list[GroupApprovalDB]:
        """List active approvals for an active group generation."""
        ...

    async def has_revoked_approval(self, group_id: int) -> bool:
        """Check if group has any explicitly revoked approvals."""
        ...

    async def requests(self, group_id: int) -> list[GroupAccessRequestDB]:
        """List nondeleted requests for an active group generation."""
        ...

    async def approve(self, group_id: int, actor: ActorContext) -> bool:
        """Add approval; already approved returns False, an invalid group raises ValueError."""
        ...

    async def revoke_approval(self, group_id: int, basis: ApprovalBasis, actor: ActorContext) -> bool:
        """Revoke an access basis; absent approval is False, an invalid group raises ValueError."""
        ...

    async def request(self, group_id: int, user_id: int, actor: ActorContext) -> int:
        """Return a pending request ID; missing active group/user generations raise ValueError."""
        ...

    async def decide(self, request_id: int, status: RequestStatus, actor: ActorContext) -> bool:
        """Decide with atomic audit/approval; missing, deleted or decided requests return False.

        PENDING is not a decision and raises ValueError, as do unavailable
        required group/user generations for an existing request.
        """
        ...

    async def delete_request(self, request_id: int, actor: ActorContext) -> bool:
        """Delete a request; missing/deleted requests are False, an invalid parent raises ValueError."""
        ...
