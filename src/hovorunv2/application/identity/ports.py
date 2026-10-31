"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from dataclasses import dataclass
from typing import Protocol

from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Platform, TargetKind
from hovorunv2.domain.user import UserDB


class UserStore(Protocol):
    """Protocol for user identity persistence operations."""

    async def get_by_id(self, user_id: int) -> UserDB | None:
        """Return an active user, or None for a missing or deleted identity."""
        ...

    async def get_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> UserDB | None:
        """Return an active user by numeric identity, or None when missing/deleted."""
        ...

    async def ingest_user(
        self, telegram_id: int, platform: str = "telegram", actor: ActorContext | None = None
    ) -> UserDB | None:
        """Ingest user from incoming evidence.

        Returns active UserDB if exists or newly created.
        Returns None if user was previously soft-deleted (tombstone-aware; does not resurrect).
        """
        ...

    async def soft_delete_user(
        self, telegram_id: int, actor: ActorContext, reason: str, platform: str = "telegram"
    ) -> bool:
        """Delete and audit an active identity; return False when already absent/deleted."""
        ...

    async def set_trust(
        self, telegram_id: int, *, is_trusted: bool, actor: ActorContext | None = None, platform: str = "telegram"
    ) -> bool:
        """Return whether an active user exists, including an unchanged trust flag."""
        ...

    async def get_historical_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> list[UserDB]:
        """List identity generations for recovery; an empty list means no history."""
        ...


@dataclass(frozen=True)
class ResolvedTarget:
    """Resolved target from public handle registry."""

    target_kind: TargetKind
    target_id: int
    handle_normalized: str
    handle_display: str
    platform: Platform = Platform.TELEGRAM


class PublicHandleStore(Protocol):
    """Protocol for active public-handle registry operations."""

    async def resolve_handle(self, handle: str, platform: Platform = Platform.TELEGRAM) -> ResolvedTarget | None:
        """Return an active public target, or None for invalid, absent or stale handles."""
        ...

    async def get_handle_by_target(
        self, target_kind: TargetKind, target_id: int, platform: Platform = Platform.TELEGRAM
    ) -> ResolvedTarget | None:
        """Return the stored handle mapping, or None when no mapping exists."""
        ...

    async def sync_handle(
        self,
        target_kind: TargetKind,
        target_id: int,
        raw_handle: str | None,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> ResolvedTarget | None:
        """Synchronize a handle with audit; return None when evidence removes it.

        Invalid nonempty handles raise InvalidHandleError. An unchanged valid
        mapping remains a successful result.
        """
        ...

    async def release_handle(
        self,
        target_kind: TargetKind,
        target_id: int,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> bool:
        """Release a handle with audit; return False when no mapping exists."""
        ...
