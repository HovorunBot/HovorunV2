"""Application boundary for durable group access bases and independent requests."""

from typing import Protocol

from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.group_grant import (
    ApprovalBasis,
    GroupAccessRequestDB,
    GroupApprovalDB,
    GroupSponsorDB,
    RequestStatus,
)


class GroupGrantStore(Protocol):
    """Audited composite commands; live activation and permission checks belong to their workflows."""

    async def approvals(self, group_id: int) -> list[GroupApprovalDB]:
        """List active approvals for an active group generation."""
        ...

    async def sponsors(self, group_id: int) -> list[GroupSponsorDB]:
        """List recorded active sponsors with active user identities, without asserting validity."""
        ...

    async def requests(self, group_id: int) -> list[GroupAccessRequestDB]:
        """List nondeleted requests for an active group generation."""
        ...

    async def approve(self, group_id: int, actor: ActorContext) -> bool:
        """Idempotently add explicit approval with audit."""
        ...

    async def revoke_approval(self, group_id: int, basis: ApprovalBasis, actor: ActorContext) -> bool:
        """Revoke an access basis while retaining history."""
        ...

    async def sponsor(self, group_id: int, user_id: int, actor: ActorContext) -> bool:
        """Idempotently record intentional sponsorship for an active identity."""
        ...

    async def revoke_sponsor(self, group_id: int, user_id: int, actor: ActorContext) -> bool:
        """Revoke one sponsorship without erasing other access bases."""
        ...

    async def request(self, group_id: int, user_id: int, actor: ActorContext) -> int:
        """Create or return a pending request without changing group status."""
        ...

    async def decide(self, request_id: int, status: RequestStatus, actor: ActorContext) -> bool:
        """Atomically decide a pending request and add explicit approval only on approval."""
        ...

    async def delete_request(self, request_id: int, actor: ActorContext) -> bool:
        """Soft-delete a request retaining its history."""
        ...
