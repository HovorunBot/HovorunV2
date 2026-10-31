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
    ADMIN_FULL_BANNED = "admin_full_banned"
    MEMBER_EVIDENCE_UNKNOWN = "member_evidence_unknown"
    NOT_APPROVED = "not_approved"
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
        """Return whether the evidence applies to the actor, target and time.

        Args:
            actor_id: Numeric Telegram identity of the actor.
            target: Selected permission target.
            now: Current aware datetime.

        Returns:
            Whether the evidence matches the actor, the target generation and the validity interval.
        """
        if self.actor_id != actor_id:
            return False
        if not self._matches_target(target):
            return False
        return self._is_current(now)

    def _matches_target(self, target: PermissionTarget) -> bool:
        """Bind evidence to the selected internal identity and configuration generation."""
        return self.target_id == target.entity_id and self.target_generation == target.generation

    def _is_current(self, now: datetime) -> bool:
        """Require aware evidence timestamps and preserve the original validity interval."""
        if self.verified_at.utcoffset() is None:
            return False
        if self.expires_at.utcoffset() is None:
            return False
        return self.verified_at <= now < self.expires_at


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
        """Validate that the allow/deny flag matches its reason code.

        Raises:
            ValueError: If the flag does not match the ALLOWED reason code.
        """
        if self.allowed != (self.reason == DecisionReason.ALLOWED):
            msg = "Allowed decisions must have reason ALLOWED; denied decisions must not."
            raise ValueError(msg)

    def __bool__(self) -> bool:
        """Allow explicit decisions to be used in boolean guards."""
        return self.allowed
