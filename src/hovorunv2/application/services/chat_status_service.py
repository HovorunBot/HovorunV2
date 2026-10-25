"""Application service for managing chat access status and group access decisions."""

from datetime import UTC, datetime

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.membership import MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import AccessDecision, DecisionReason
from hovorunv2.application.ports import ChatStore
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, TargetKind


def evidence_time(value: datetime) -> datetime:
    """Normalize timestamps read from SQLite without timezone metadata."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ChatStatusService:
    """Service to handle chat lifecycle, durable approvals and access blockers.

    This service evaluates group access against durable explicit/legacy approvals,
    group bans, and current-administrator full bans with automatic recovery.
    """

    def __init__(
        self,
        chat_service: ChatStore,
        command_service: CommandService,
        grant_store: GroupGrantStore,
        ban_store: BanStore,
        membership_store: MembershipStore,
    ) -> None:
        """Initialize the service with mandatory store dependencies."""
        self._chat_service = chat_service
        self._command_service = command_service
        self._grant_store = grant_store
        self._ban_store = ban_store
        self._membership_store = membership_store

    async def get_status(self, chat_id: int) -> ChatStatus:
        """Get the current lifecycle status of a chat."""
        chat = await self._chat_service.get_chat(chat_id)
        if not chat:
            return ChatStatus.UNAUTHORIZED
        return ChatStatus(chat.status)

    async def set_status(self, chat_id: int, status: ChatStatus) -> None:
        """Update the lifecycle status of a chat."""
        await self._chat_service.update_status(chat_id, status)

    async def approve(self, chat_id: int) -> None:
        """Approve a chat and enable its default command set."""
        await self.set_status(chat_id, ChatStatus.APPROVED)
        await self._command_service.enable_defaults(chat_id)

    async def _check_group_ban(self, chat: ChatDB) -> AccessDecision | None:
        """Group bans have absolute precedence and cannot be bypassed."""
        if chat.status == ChatStatus.BANNED:
            return AccessDecision(allowed=False, reason=DecisionReason.GROUP_BANNED)
        group_bans = await self._ban_store.list_active(TargetKind.GROUP, chat.chat_id)
        if any(b.kind == BanKind.FULL.value for b in group_bans):
            return AccessDecision(allowed=False, reason=DecisionReason.GROUP_BANNED)
        return None

    async def _check_admin_blockers(self, group_id: int, current_time: datetime) -> AccessDecision | None:
        """Check administrator evidence freshness and full-ban suspension on current admins."""
        state = await self._membership_store.get_state(group_id)
        if state is not None and state.admins_verified_at is not None:
            if not state.admins_known:
                return AccessDecision(allowed=False, reason=DecisionReason.MEMBER_EVIDENCE_UNKNOWN)
            if state.admins_expires_at is not None and evidence_time(state.admins_expires_at) <= current_time:
                return AccessDecision(allowed=False, reason=DecisionReason.MEMBER_EVIDENCE_UNKNOWN)

        relevant_users = await self._membership_store.relevant_users(group_id, limit=100)
        has_unknown_banned_candidate = False

        for user in relevant_users:
            user_bans = await self._ban_store.list_active(TargetKind.USER, user.telegram_id)
            if not any(b.kind == BanKind.FULL.value for b in user_bans):
                continue

            evidence = await self._membership_store.get_member(group_id, user.id)
            if (
                evidence is None
                or evidence.status == MembershipStatus.UNKNOWN
                or evidence_time(evidence.expires_at) <= current_time
            ):
                has_unknown_banned_candidate = True
            elif evidence.status == MembershipStatus.ADMIN:
                return AccessDecision(allowed=False, reason=DecisionReason.ADMIN_FULL_BANNED)

        if has_unknown_banned_candidate:
            return AccessDecision(allowed=False, reason=DecisionReason.MEMBER_EVIDENCE_UNKNOWN)
        return None

    async def _check_approvals(self, chat: ChatDB) -> AccessDecision:
        """Verify durable explicit or legacy approval validity."""
        approvals = await self._grant_store.approvals(chat.id)
        if approvals:
            return AccessDecision(allowed=True, reason=DecisionReason.ALLOWED)

        has_revoked = await self._grant_store.has_revoked_approval(chat.id)
        if not has_revoked and chat.status == ChatStatus.APPROVED:
            return AccessDecision(allowed=True, reason=DecisionReason.ALLOWED)

        return AccessDecision(allowed=False, reason=DecisionReason.NOT_APPROVED)

    async def evaluate_access(self, chat_id: int, now: datetime | None = None) -> AccessDecision:
        """Evaluate durable approval, bans and administrator blockers for a group.

        Order of evaluation:
        1. Target entity active generation check.
        2. Group ban precedence (independent of requesting actor or approvals).
        3. Administrator evidence freshness and full-ban suspension on current admins.
        4. Durable explicit or legacy approval validity.
        """
        current_time = now if now is not None else datetime.now(UTC)
        chat = await self._chat_service.get_chat(chat_id)
        if chat is None or chat.deleted_at is not None:
            return AccessDecision(allowed=False, reason=DecisionReason.ENTITY_UNAVAILABLE)

        ban_decision = await self._check_group_ban(chat)
        if ban_decision is not None:
            return ban_decision

        admin_decision = await self._check_admin_blockers(chat.id, current_time)
        if admin_decision is not None:
            return admin_decision

        return await self._check_approvals(chat)

    async def is_approved(self, chat_id: int) -> bool:
        """Check if chat is approved and free of active blockers."""
        decision = await self.evaluate_access(chat_id)
        return decision.allowed
