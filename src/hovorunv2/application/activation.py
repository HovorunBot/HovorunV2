"""Group activation commands and the atomic persistence boundary."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.permissions import GroupAdminEvidence, PermissionTarget


class ActivationAction(StrEnum):
    """Intentional group activation or private request administration."""

    ENABLE = "enable"
    APPROVE = "approve"
    REJECT = "reject"


class ActivationOutcome(StrEnum):
    """Safe outcomes for activation presentation and notification deduplication."""

    APPROVED = "approved"
    ALREADY_APPROVED = "already_approved"
    REQUESTED = "requested"
    ALREADY_PENDING = "already_pending"
    REJECTED = "rejected"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    GROUP_BANNED = "group_banned"
    STALE_REQUEST = "stale_request"


@dataclass(frozen=True, slots=True)
class ActivationRequest:
    """Bind trusted transport facts, actor and the selected group generation."""

    actor_id: int
    target: PermissionTarget
    transport_kind: str
    action: ActivationAction = ActivationAction.ENABLE
    request_id: int | None = None
    group_admin: GroupAdminEvidence | None = None


@dataclass(frozen=True, slots=True)
class ActivationResult:
    """Return a durable request reference without user/profile/message payloads."""

    outcome: ActivationOutcome
    request_id: int | None = None


class ActivationStore(Protocol):
    """Recheck current authority and atomically persist activation, defaults and audit."""

    async def execute(self, request: ActivationRequest) -> ActivationResult:
        """Reject stale identities, generations, authority and bans inside the mutation transaction."""
        ...
