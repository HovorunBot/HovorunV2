"""Persistence contract for independent restrictions."""

from typing import Protocol

from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB, BanKind
from hovorunv2.domain.enums import TargetKind


class BanStore(Protocol):
    """Store stable-subject restrictions with atomic audit and policy revision."""

    async def list_active(self, target: TargetKind, telegram_id: int) -> list[BanDB]:
        """List restrictions for an active entity; missing/deleted identities yield an empty list."""
        ...

    async def policy_revision(self, target: TargetKind, telegram_id: int) -> int:
        """Return the monotonic revision including revoked history, or zero without history."""
        ...

    async def set_ban(
        self,
        target: TargetKind,
        telegram_id: int,
        kind: BanKind,
        *,
        active: bool,
        actor: ActorContext,
        public_explanation: str | None = None,
    ) -> bool:
        """Apply or revoke a restriction and report whether it changed.

        Invalid targets or restriction combinations raise ValueError; rejected
        authority or protected-target changes raise PermissionError.

        Args:
            target: Kind of the restricted entity.
            telegram_id: Numeric Telegram identity of the restricted entity.
            kind: Restriction kind to apply or revoke.
            active: Whether to apply (True) or revoke (False) the restriction.
            actor: Audited actor performing the change.
            public_explanation: Optional user-visible reason for the restriction.

        Returns:
            Whether the restriction state changed.
        """
        ...
