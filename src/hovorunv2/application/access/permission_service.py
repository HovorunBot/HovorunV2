"""Collect current facts and enforce domain guards before permission evaluation."""

from datetime import UTC, datetime

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_policy import authority_allowed, permission_decision
from hovorunv2.application.access.permissions import (
    AccessDecision,
    DecisionReason,
    PermissionRequest,
)
from hovorunv2.application.ports import AuthorizationPolicy, ChatStore, UserStore
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import ChatStatus, TargetKind


class PermissionService:
    """Evaluate current database authority and explicit protected actions."""

    def __init__(
        self,
        users: UserStore,
        chats: ChatStore,
        policy: AuthorizationPolicy,
        ban_store: BanStore,
    ) -> None:
        self._users = users
        self._chats = chats
        self._policy = policy
        self._ban_store = ban_store

    async def has_authority(self, actor_id: int, action: str) -> bool:
        """Query current bot authority for legacy gates; execution still needs action/target guards."""
        user = await self._users.get_by_telegram_id(actor_id)
        return authority_allowed(self._policy, user.role if user is not None else None, action)

    async def _evaluate_target(self, request: PermissionRequest) -> AccessDecision:
        """Return a concrete transport and resource availability decision."""
        target = request.target
        if target.kind == "bot" and request.transport_kind != "private":
            return AccessDecision(allowed=False, reason=DecisionReason.UNAUTHORIZED_MANAGEMENT)
        if target.kind != "group":
            return AccessDecision(allowed=True, reason=DecisionReason.ALLOWED)
        return await self._evaluate_group_target(request)

    async def _evaluate_group_target(self, request: PermissionRequest) -> AccessDecision:
        """Check the selected group's current generation and full-ban state."""
        target = request.target
        if target.telegram_id is None:
            return AccessDecision(allowed=False, reason=DecisionReason.ENTITY_UNAVAILABLE)
        chat = await self._chats.get_chat(target.telegram_id, request.platform.value)
        if chat is None or chat.id != target.entity_id or chat.generation != target.generation:
            return AccessDecision(allowed=False, reason=DecisionReason.ENTITY_UNAVAILABLE)
        if chat.status == ChatStatus.BANNED:
            return AccessDecision(allowed=False, reason=DecisionReason.GROUP_BANNED)
        group_bans = await self._ban_store.list_active(TargetKind.GROUP, chat.chat_id)
        if any(b.kind == BanKind.FULL.value for b in group_bans):
            return AccessDecision(allowed=False, reason=DecisionReason.GROUP_BANNED)
        return AccessDecision(allowed=True, reason=DecisionReason.ALLOWED)

    async def evaluate(self, request: PermissionRequest) -> AccessDecision:
        """Read fresh active identities; owners cannot bypass resource blockers."""
        user = await self._users.get_by_telegram_id(request.actor_id, request.platform.value)
        if user is None:
            return AccessDecision(allowed=False, reason=DecisionReason.ENTITY_UNAVAILABLE)
        target_decision = await self._evaluate_target(request)
        if not target_decision.allowed:
            return target_decision
        return permission_decision(
            request,
            user.role,
            self._policy,
            datetime.now(UTC),
            target_active=True,
            group_banned=False,
        )
