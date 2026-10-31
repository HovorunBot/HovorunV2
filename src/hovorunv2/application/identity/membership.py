"""Narrow Telegram membership evidence contracts without transport models."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class MembershipStatus(StrEnum):
    """Unknown verification never proves membership or absence."""

    UNKNOWN = "unknown"
    MEMBER = "member"
    ADMIN = "admin"
    LEFT = "left"


@dataclass(frozen=True, slots=True)
class MembershipEvidence:
    """API evidence tied to numeric identity; persistence binds the active generation."""

    group_telegram_id: int
    user_telegram_id: int
    status: MembershipStatus
    verified_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AdministratorEvidence:
    """A failed administrator lookup remains explicitly unknown, even with no rows."""

    group_telegram_id: int
    known: bool
    members: tuple[MembershipEvidence, ...]
    verified_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BotCapabilityEvidence:
    """Bot presence and individual administrator rights, unknown when not verified."""

    membership: MembershipEvidence
    can_delete_messages: bool | None
    can_restrict_members: bool | None
    can_invite_users: bool | None
    can_pin_messages: bool | None
    can_manage_chat: bool | None


class MembershipPort(Protocol):
    """Verify only requested members, administrators, and bot capabilities."""

    async def verify_member(self, group_telegram_id: int, user_telegram_id: int) -> MembershipEvidence:
        """Return current API evidence; API uncertainty returns UNKNOWN."""
        ...

    async def list_administrators(self, group_telegram_id: int) -> AdministratorEvidence:
        """Return administrator evidence; known=False denotes API uncertainty, even with no rows."""
        ...

    async def verify_bot(self, group_telegram_id: int) -> BotCapabilityEvidence:
        """Return bot evidence; UNKNOWN membership and None rights preserve API uncertainty."""
        ...
