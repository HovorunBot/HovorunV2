"""Application service for managing chat access status and group access decisions."""

from datetime import UTC, datetime

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.group_policy import GroupAccessFacts, group_access
from hovorunv2.application.membership import MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import AccessDecision
from hovorunv2.application.ports import ChatStore
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import ChatStatus, TargetKind


def evidence_time(value: datetime) -> datetime:
    """Validate aware PostgreSQL facts and normalize their UTC representation."""
    if value.utcoffset() is None:
        msg = "Membership facts require a timezone-aware timestamp."
        raise ValueError(msg)
    return value.astimezone(UTC)


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

    async def evaluate_access(self, chat_id: int, now: datetime | None = None) -> AccessDecision:
        """Collect current group facts and apply the shared approval/blocker precedence."""
        current_time = now if now is not None else datetime.now(UTC)
        chat = await self._chat_service.get_chat(chat_id)
        if chat is None:
            return group_access(GroupAccessFacts())
        bans = await self._ban_store.list_active(TargetKind.GROUP, chat.chat_id)
        state = await self._membership_store.get_state(chat.id)
        fresh = bool(
            state is not None
            and state.admins_known
            and state.admins_verified_at is not None
            and state.admins_expires_at is not None
            and evidence_time(state.admins_verified_at) <= current_time < evidence_time(state.admins_expires_at)
        )
        blocker = await self._membership_store.full_banned_admin_status(chat.id, current_time)
        approvals = await self._grant_store.approvals(chat.id)
        revoked = await self._grant_store.has_revoked_approval(chat.id)
        return group_access(
            GroupAccessFacts(
                active=chat.deleted_at is None,
                banned=chat.status == ChatStatus.BANNED or any(b.kind == BanKind.FULL for b in bans),
                admins_fresh=fresh,
                banned_admin=blocker == MembershipStatus.ADMIN,
                unknown_admin=blocker == MembershipStatus.UNKNOWN,
                approved=bool(approvals) or (not revoked and chat.status == ChatStatus.APPROVED),
            )
        )

    async def is_approved(self, chat_id: int) -> bool:
        """Check if chat is approved and free of active blockers."""
        decision = await self.evaluate_access(chat_id)
        return decision.allowed
