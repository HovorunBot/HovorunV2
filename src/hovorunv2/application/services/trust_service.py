"""Audited boolean trust administration, independent of authority roles."""

from hovorunv2.application.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.ports import AuthorizationPolicy, PublicHandleStore, UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ActorOrigin, TargetKind

MAX_USER_ID = (1 << 63) - 1


class TrustService:
    """Resolve active users and restrict trust changes to CLI or private bot administration."""

    def __init__(self, users: UserStore, handles: PublicHandleStore, policy: AuthorizationPolicy) -> None:
        self._users = users
        self._handles = handles
        self._policy = policy

    async def set_trust(
        self,
        target: str,
        *,
        is_trusted: bool,
        actor: ActorContext,
        transport_kind: str | None = None,
    ) -> bool:
        """Change an existing active user's flag; return whether the identity exists.

        Actor origin and transport are trusted interface facts, never callback data.
        UserDB.trust_revision advances only on a change, in the audit transaction.
        """
        await self._authorize(actor, transport_kind)
        user_id = await self._resolve_user(target)
        return await self._users.set_trust(user_id, is_trusted=is_trusted, actor=actor)

    async def _authorize(self, actor: ActorContext, transport_kind: str | None) -> None:
        """Require current bot administration when the actor is not the CLI."""
        if actor.origin == ActorOrigin.CLI:
            return
        user = await self._users.get_by_telegram_id(actor.actor_id) if actor.actor_id is not None else None
        if (
            actor.origin != ActorOrigin.TELEGRAM
            or transport_kind != "private"
            or user is None
            or not self._policy.is_allowed(PermissionFacts(user.role, "bot:administer", PermissionTarget("bot")))
        ):
            msg = "Trust administration requires CLI or a bot administrator in private transport."
            raise PermissionError(msg)

    async def _resolve_user(self, target: str) -> int:
        if target.startswith("@"):
            resolved = await self._handles.resolve_handle(target)
            if resolved is None or resolved.target_kind != TargetKind.USER:
                msg = "No active user has that known handle."
                raise ValueError(msg)
            return resolved.target_id
        try:
            user_id = int(target)
        except ValueError as error:
            msg = "Use a positive numeric Telegram user ID or a known @username."
            raise ValueError(msg) from error
        if not 0 < user_id <= MAX_USER_ID:
            msg = "Telegram user ID must be positive and fit in a signed 64-bit integer."
            raise ValueError(msg)
        return user_id
