"""Persistence contract for independent restrictions."""

from typing import Protocol

from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB, BanKind
from hovorunv2.domain.enums import TargetKind


class BanStore(Protocol):
    """Store stable-subject restrictions with atomic audit and policy revision."""

    async def list_active(self, target: TargetKind, telegram_id: int) -> list[BanDB]:
        """Return restrictions for an active entity."""
        ...

    async def policy_revision(self, target: TargetKind, telegram_id: int) -> int:
        """Return monotonic subject revision, including revoked history."""
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
        """Apply or revoke a restriction without changing roles or grants."""
        ...
