"""Application-owned inputs and results for explicit permission checks."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from hovorunv2.domain.enums import Platform, Role


class DecisionReason(StrEnum):
    """Reasons implemented by the additive permission foundation."""

    ALLOWED = "allowed"
    ENTITY_UNAVAILABLE = "entity_unavailable"
    GROUP_BANNED = "group_banned"
    UNAUTHORIZED_MANAGEMENT = "unauthorized_management"


@dataclass(frozen=True, slots=True)
class PermissionTarget:
    """Selected resource, independent of the chat carrying a command."""

    kind: str
    entity_id: int | None = None
    generation: int = 1
    telegram_id: int | None = None


@dataclass(frozen=True, slots=True)
class GroupAdminEvidence:
    """Verified administrator evidence for exactly one actor and group generation."""

    actor_id: int
    target_id: int
    target_generation: int
    verified_at: datetime
    expires_at: datetime

    def applies_to(self, actor_id: int, target: PermissionTarget, now: datetime) -> bool:
        """Reject mismatched, expired, future or timezone-ambiguous evidence."""
        return (
            self.actor_id == actor_id
            and self.target_id == target.entity_id
            and self.target_generation == target.generation
            and self.verified_at.utcoffset() is not None
            and self.expires_at.utcoffset() is not None
            and self.verified_at <= now < self.expires_at
        )


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Protected human action; bootstrap/recovery retain separate explicit paths."""

    actor_id: int
    action: str
    target: PermissionTarget
    platform: Platform = Platform.TELEGRAM
    group_admin: GroupAdminEvidence | None = None
    transport_kind: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionFacts:
    """Current authority and verified scope facts supplied to the pure policy adapter."""

    role: Role
    action: str
    target: PermissionTarget
    group_admin: bool = False


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """Immutable authorization result with a deterministic reason."""

    allowed: bool
    reason: DecisionReason

    def __post_init__(self) -> None:
        """Keep allow/deny flags consistent with their reason code."""
        if self.allowed != (self.reason == DecisionReason.ALLOWED):
            msg = "Allowed decisions must have reason ALLOWED; denied decisions must not."
            raise ValueError(msg)

    def __bool__(self) -> bool:
        """Allow explicit decisions to be used in boolean guards."""
        return self.allowed
