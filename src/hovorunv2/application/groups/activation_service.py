"""Resolve activation targets and obtain only required current administrator evidence."""

from hovorunv2.application.access.permissions import GroupAdminEvidence, PermissionFacts, PermissionTarget
from hovorunv2.application.groups.activation import (
    ActivationAction,
    ActivationRequest,
    ActivationResult,
    ActivationStore,
)
from hovorunv2.application.identity.membership import MembershipPort, MembershipStatus
from hovorunv2.application.ports import AuthorizationPolicy, ChatStore, UserStore
from hovorunv2.domain.user import UserDB


class GroupActivationService:
    """Keep intentional activation separate from group feature availability."""

    def __init__(
        self,
        chats: ChatStore,
        users: UserStore,
        membership: MembershipPort,
        policy: AuthorizationPolicy,
        store: ActivationStore,
    ) -> None:
        self._chats = chats
        self._users = users
        self._membership = membership
        self._policy = policy
        self._store = store

    async def target(self, telegram_id: int) -> PermissionTarget | None:
        """Resolve only an existing active group, never provision it from callback data."""
        chat = await self._chats.get_chat(telegram_id)
        if chat is None or chat.chat_type not in {"group", "supergroup"}:
            return None
        return PermissionTarget("group", chat.id, chat.generation, chat.chat_id)

    async def enable(self, actor_id: int, target: PermissionTarget, transport_kind: str) -> ActivationResult:
        """Trusted users need no Telegram admin rights; untrusted requesters need fresh evidence."""
        evidence = await self._group_admin_evidence(actor_id, target, transport_kind)
        return await self._store.execute(ActivationRequest(actor_id, target, transport_kind, group_admin=evidence))

    async def _group_admin_evidence(
        self, actor_id: int, target: PermissionTarget, transport_kind: str
    ) -> GroupAdminEvidence | None:
        """Verify current Telegram administrator evidence only when activation requires it."""
        user = await self._users.get_by_telegram_id(actor_id)
        if not self._requires_group_admin(user):
            return None
        if target.telegram_id is None or target.entity_id is None:
            return None
        if transport_kind not in {"group", "supergroup"}:
            return None
        member = await self._membership.verify_member(target.telegram_id, actor_id)
        if member.status != MembershipStatus.ADMIN:
            return None
        if member.user_telegram_id != actor_id or member.group_telegram_id != target.telegram_id:
            return None
        return GroupAdminEvidence(actor_id, target.entity_id, target.generation, member.verified_at, member.expires_at)

    def _requires_group_admin(self, user: UserDB | None) -> bool:
        """Only known untrusted actors without bot administration require Telegram evidence."""
        if user is None or user.is_trusted:
            return False
        return not self._policy.is_allowed(PermissionFacts(user.role, "bot:administer", PermissionTarget("bot")))

    async def decide(
        self, actor_id: int, target: PermissionTarget, request_id: int, action: ActivationAction, transport_kind: str
    ) -> ActivationResult:
        """Recheck private administrative decisions against the durable pending request."""
        return await self._store.execute(ActivationRequest(actor_id, target, transport_kind, action, request_id))
