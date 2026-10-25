"""Durable, generation-bound membership evidence used by policy and reconciliation."""

from datetime import datetime
from typing import Protocol

from hovorunv2.application.membership import AdministratorEvidence, BotCapabilityEvidence, MembershipEvidence
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.membership import GroupMembershipStateDB
from hovorunv2.domain.user import UserDB


class MembershipStore(Protocol):
    """Persist operational evidence with audit and policy revision invalidation."""

    async def due_groups(self, now: datetime, limit: int = 50) -> list[ChatDB]:
        """Bounded active groups with missing/expired bot, admin or relevant user evidence."""
        ...

    async def relevant_users(self, group_id: int, limit: int = 50) -> list[UserDB]:
        """Return current and unresolved former administrators, missing/stale evidence first."""
        ...

    async def get_member(self, group_id: int, user_id: int) -> MembershipEvidence | None:
        """Read active-generation evidence; callers must check its explicit expiry."""
        ...

    async def get_state(self, group_id: int) -> GroupMembershipStateDB | None:
        """Read current bot/admin freshness and policy revision for an active generation."""
        ...

    async def is_admin_any(self, user_telegram_id: int, now: datetime) -> bool:
        """Check if user is verified administrator in any active group."""
        ...

    async def save_member(
        self,
        group_id: int,
        evidence: MembershipEvidence,
        actor: ActorContext | None = None,
        *,
        expected_user_id: int | None = None,
    ) -> bool:
        """Persist relevant evidence, rejecting stale or mismatched identity updates."""
        ...

    async def save_bot(self, group_id: int, evidence: BotCapabilityEvidence, actor: ActorContext | None = None) -> bool:
        """Update physical presence and individual capabilities without changing grants/bans."""
        ...

    async def save_admins(
        self, group_id: int, evidence: AdministratorEvidence, actor: ActorContext | None = None
    ) -> bool:
        """Persist current administrator evidence; failure retains prior candidates as UNKNOWN."""
        ...
