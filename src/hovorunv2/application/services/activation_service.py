"""Resolve activation targets and obtain only required current administrator evidence."""

from hovorunv2.application.activation import (
    ActivationAction,
    ActivationRequest,
    ActivationResult,
    ActivationStore,
)
from hovorunv2.application.membership import MembershipPort, MembershipStatus
from hovorunv2.application.permissions import GroupAdminEvidence, PermissionFacts, PermissionTarget
from hovorunv2.application.ports import AuthorizationPolicy, ChatStore, UserStore


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
        user = await self._users.get_by_telegram_id(actor_id)
        evidence = None
        if (
            user is not None
            and not user.is_trusted
            and not self._policy.is_allowed(PermissionFacts(user.role, "bot:administer", PermissionTarget("bot")))
            and target.telegram_id is not None
            and target.entity_id is not None
            and transport_kind in {"group", "supergroup"}
        ):
            member = await self._membership.verify_member(target.telegram_id, actor_id)
            if (
                member.status == MembershipStatus.ADMIN
                and member.user_telegram_id == actor_id
                and member.group_telegram_id == target.telegram_id
            ):
                evidence = GroupAdminEvidence(
                    actor_id, target.entity_id, target.generation, member.verified_at, member.expires_at
                )
        return await self._store.execute(ActivationRequest(actor_id, target, transport_kind, group_admin=evidence))

    async def decide(
        self, actor_id: int, target: PermissionTarget, request_id: int, action: ActivationAction, transport_kind: str
    ) -> ActivationResult:
        """Recheck private administrative decisions against the durable pending request."""
        return await self._store.execute(ActivationRequest(actor_id, target, transport_kind, action, request_id))
