"""Audited access restriction administration, separate from runtime evaluation."""

import logging

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB, BanKind
from hovorunv2.domain.enums import ActorOrigin, TargetKind

logger = logging.getLogger(__name__)


class BanService:
    """Expose independent restrictions to trusted CLI and authenticated administration."""

    def __init__(self, bans: BanStore, permissions: PermissionService) -> None:
        self._bans = bans
        self._permissions = permissions

    async def _authorize(self, actor: ActorContext, transport_kind: str | None) -> None:
        """Require CLI or private bot administration before changing restrictions.

        Raises:
            PermissionError: If the actor is not the CLI or an authorized private bot administrator.
        """
        if actor.origin == ActorOrigin.CLI:
            return
        msg = "Ban administration requires CLI or a bot administrator in private transport."
        if actor.origin != ActorOrigin.TELEGRAM or transport_kind != "private" or actor.actor_id is None:
            raise PermissionError(msg)
        if not await self._permissions.has_authority(actor.actor_id, "bot:administer"):
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
        expected_generation: int | None = None,
        transport_kind: str | None = None,
    ) -> bool:
        """Apply a restriction after authorizing the actor.

        Args:
            target: Kind of the restricted entity.
            telegram_id: Numeric Telegram identity of the restricted entity.
            kind: Restriction kind to apply.
            actor: Audited actor performing the change.
            public_explanation: Optional user-visible reason for the restriction.
            expected_generation: Optional previewed identity generation to enforce under lock.
            transport_kind: Transport the actor used, required for Telegram authorization.

        Returns:
            Whether the restriction changed.
        """
        await self._authorize(actor, transport_kind)
        changed = await self._bans.set_ban(
            target,
            telegram_id,
            kind,
            active=True,
            actor=actor,
            public_explanation=public_explanation,
            expected_generation=expected_generation,
        )
        logger.info(
            "Access restriction applied: target=%s target_id=%d kind=%s changed=%s",
            target.value,
            telegram_id,
            kind.value,
            changed,
        )
        return changed

    async def revoke(
        self,
        target: TargetKind,
        telegram_id: int,
        kind: BanKind,
        actor: ActorContext,
        *,
        expected_generation: int | None = None,
        transport_kind: str | None = None,
    ) -> bool:
        """Revoke a restriction after authorizing the actor and retain its audit history.

        Args:
            target: Kind of the restricted entity.
            telegram_id: Numeric Telegram identity of the restricted entity.
            kind: Restriction kind to revoke.
            actor: Audited actor performing the change.
            expected_generation: Optional previewed identity generation to enforce under lock.
            transport_kind: Transport the actor used, required for Telegram authorization.

        Returns:
            Whether the restriction changed.
        """
        await self._authorize(actor, transport_kind)
        changed = await self._bans.set_ban(
            target,
            telegram_id,
            kind,
            active=False,
            actor=actor,
            expected_generation=expected_generation,
        )
        logger.info(
            "Access restriction revoked: target=%s target_id=%d kind=%s changed=%s",
            target.value,
            telegram_id,
            kind.value,
            changed,
        )
        return changed
