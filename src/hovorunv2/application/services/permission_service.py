"""Collect current facts and enforce domain guards before permission evaluation."""

from datetime import UTC, datetime

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.permissions import (
    AccessDecision,
    DecisionReason,
    PermissionFacts,
    PermissionRequest,
    PermissionTarget,
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
        return user is not None and self._policy.is_allowed(PermissionFacts(user.role, action, PermissionTarget("bot")))

    async def _check_target_entity(self, request: PermissionRequest) -> AccessDecision | None:
        target = request.target
        if target.kind == "bot" and request.transport_kind != "private":
            return AccessDecision(allowed=False, reason=DecisionReason.UNAUTHORIZED_MANAGEMENT)
        if target.kind == "group":
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
        return None

    async def evaluate(self, request: PermissionRequest) -> AccessDecision:
        """Read fresh active identities; owners cannot bypass resource blockers."""
        user = await self._users.get_by_telegram_id(request.actor_id, request.platform.value)
        if user is None:
            return AccessDecision(allowed=False, reason=DecisionReason.ENTITY_UNAVAILABLE)
        target_decision = await self._check_target_entity(request)
        if target_decision is not None:
            return target_decision
        is_group_admin = (
            request.target.kind == "group"
            and request.group_admin is not None
            and request.group_admin.applies_to(request.actor_id, request.target, datetime.now(UTC))
        )
        allowed = self._policy.is_allowed(PermissionFacts(user.role, request.action, request.target, is_group_admin))
        return AccessDecision(allowed, DecisionReason.ALLOWED if allowed else DecisionReason.UNAUTHORIZED_MANAGEMENT)
