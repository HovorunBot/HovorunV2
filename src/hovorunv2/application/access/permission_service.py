"""Collect current facts and enforce domain guards before permission evaluation."""

from datetime import UTC, datetime

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_policy import authority_allowed, permission_decision
from hovorunv2.application.access.permissions import (
    AccessDecision,
    DecisionReason,
    PermissionRequest,
)
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
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
        """Return whether the actor holds current authority for the action.

        Args:
            actor_id: Numeric Telegram identity of the actor.
            action: Permission action to check.

        Returns:
            Whether the actor's active role permits the action.
        """
        user = await self._users.get_by_telegram_id(actor_id)
        return authority_allowed(self._policy, user.role if user is not None else None, action)

    async def _evaluate_target(self, request: PermissionRequest) -> AccessDecision:
        """Return the transport and resource availability decision for the target.

        Args:
            request: Permission request to evaluate.

        Returns:
            Concrete allowed or denied decision for the target kind.
        """
        target = request.target
        if target.kind == "bot" and request.transport_kind != "private":
            return AccessDecision(allowed=False, reason=DecisionReason.UNAUTHORIZED_MANAGEMENT)
        if target.kind != "group":
            return AccessDecision(allowed=True, reason=DecisionReason.ALLOWED)
        return await self._evaluate_group_target(request)

    async def _evaluate_group_target(self, request: PermissionRequest) -> AccessDecision:
        """Return the selected group's generation and full-ban decision.

        Args:
            request: Permission request whose target is a group.

        Returns:
            Allowed decision, or a denial for an unavailable, stale or banned group.
        """
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
        """Evaluate the request against a fresh active identity and current target state.

        Args:
            request: Permission request to evaluate.

        Returns:
            Allowed decision, or a denial for an unavailable identity or blocked target.
        """
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
