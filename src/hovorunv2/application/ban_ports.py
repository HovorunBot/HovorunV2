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
        """Return whether a restriction changed; an already applied/revoked state is a no-op.

        Invalid targets or restriction combinations raise ValueError; rejected
        authority or protected-target changes raise PermissionError.
        """
        ...
