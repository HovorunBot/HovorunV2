"""Shared current-authority check for private/CLI durable administration mutations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.access.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.enums import ActorOrigin, TargetKind
from hovorunv2.domain.user import UserDB


async def require_private_administrator(
    session: AsyncSession,
    actor: ActorContext,
    transport_kind: str | None,
    policy: AuthorizationPolicy,
    *,
    message: str,
) -> None:
    """Require CLI origin or a current, unbanned, private bot administrator.

    Args:
        session: Database session for the current user and ban lookups.
        actor: Actor context identifying origin and Telegram identity.
        transport_kind: Ingress transport; only private transport authorizes a bot administrator.
        policy: Authorization policy deciding bot-administration authority.
        message: Permission error message reused for every rejection.

    Raises:
        PermissionError: If the actor is not CLI origin or a current, unbanned, private bot administrator.
    """
    if actor.origin == ActorOrigin.CLI:
        return
    if actor.origin != ActorOrigin.TELEGRAM or transport_kind != "private":
        raise PermissionError(message)
    user = await session.scalar(
        select(UserDB).where(
            UserDB.telegram_id == actor.actor_id,
            UserDB.platform == "telegram",
            UserDB.deleted_at.is_(None),
        )
    )
    banned = await session.scalar(
        select(BanDB.id)
        .where(
            BanDB.target_kind == TargetKind.USER,
            BanDB.telegram_id == actor.actor_id,
            BanDB.platform == "telegram",
            BanDB.deleted_at.is_(None),
            BanDB.revoked_at.is_(None),
        )
        .limit(1)
    )
    if user is None or banned is not None:
        raise PermissionError(message)
    if not policy.is_allowed(PermissionFacts(user.role, "bot:administer", PermissionTarget("bot"))):
        raise PermissionError(message)
