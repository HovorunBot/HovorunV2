"""Audited access restriction administration, separate from runtime evaluation."""

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.services.permission_service import PermissionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB, BanKind
from hovorunv2.domain.enums import ActorOrigin, TargetKind


class BanService:
    """Expose independent restrictions to trusted CLI and authenticated administration."""

    def __init__(self, bans: BanStore, permissions: PermissionService) -> None:
        self._bans = bans
        self._permissions = permissions

    async def _authorize(self, actor: ActorContext, transport_kind: str | None) -> None:
        if actor.origin == ActorOrigin.CLI:
            return
        if (
            actor.origin != ActorOrigin.TELEGRAM
            or transport_kind != "private"
            or actor.actor_id is None
            or not await self._permissions.has_authority(actor.actor_id, "bot:administer")
        ):
            msg = "Ban administration requires CLI or a bot administrator in private transport."
            raise PermissionError(msg)

    async def list_active(self, target: TargetKind, telegram_id: int) -> list[BanDB]:
        """Inspect current restrictions without revealing internal notes publicly."""
        return await self._bans.list_active(target, telegram_id)

    async def apply(
        self,
        target: TargetKind,
        telegram_id: int,
        kind: BanKind,
        actor: ActorContext,
        public_explanation: str | None = None,
        *,
        transport_kind: str | None = None,
    ) -> bool:
        """Apply a restriction, requiring a separate internal reason."""
        await self._authorize(actor, transport_kind)
        return await self._bans.set_ban(
            target,
            telegram_id,
            kind,
            active=True,
            actor=actor,
            public_explanation=public_explanation,
        )

    async def revoke(
        self,
        target: TargetKind,
        telegram_id: int,
        kind: BanKind,
        actor: ActorContext,
        *,
        transport_kind: str | None = None,
    ) -> bool:
        """Revoke a restriction while retaining its record and audit history."""
        await self._authorize(actor, transport_kind)
        return await self._bans.set_ban(target, telegram_id, kind, active=False, actor=actor)
