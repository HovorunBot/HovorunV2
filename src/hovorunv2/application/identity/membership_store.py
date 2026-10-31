"""Durable, generation-bound membership evidence used by policy and reconciliation."""

from datetime import datetime
from typing import Protocol

from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
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

    async def full_banned_admin_status(self, group_id: int, now: datetime) -> MembershipStatus | None:
        """Return the full-ban administrator blocker status for the group.

        None means no full-banned administrator candidate requires blocking or
        verification. The query is not truncated by maintenance batch sizes.

        Args:
            group_id: Internal identity of the group.
            now: Current aware datetime.

        Returns:
            ADMIN for a verified blocker, UNKNOWN for unresolved evidence, or None when no blocker exists.
        """
        ...

    async def get_member(self, group_id: int, user_id: int) -> MembershipEvidence | None:
        """Return active-generation evidence or None; callers still check explicit expiry."""
        ...

    async def get_state(self, group_id: int) -> GroupMembershipStateDB | None:
        """Return active-generation freshness/revision state, or None when no state exists."""
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
        """Persist member evidence and report whether relevant evidence changed.

        Mismatched group routing raises ValueError. expected_user_id optionally
        binds the observed identity to the caller's current generation.

        Args:
            group_id: Internal identity of the group.
            evidence: Member evidence to persist.
            actor: Optional audited actor performing the save.
            expected_user_id: Optional expected internal user identity for generation binding.

        Returns:
            Whether relevant evidence changed; stale or inapplicable updates return False.
        """
        ...

    async def save_bot(self, group_id: int, evidence: BotCapabilityEvidence, actor: ActorContext | None = None) -> bool:
        """Return whether bot evidence changed; stale/inapplicable evidence is a no-op."""
        ...

    async def save_admins(
        self, group_id: int, evidence: AdministratorEvidence, actor: ActorContext | None = None
    ) -> bool:
        """Persist administrator evidence and report whether it changed.

        Unknown API evidence preserves prior candidates as UNKNOWN; this state
        is distinct from a persistence failure, which raises.

        Args:
            group_id: Internal identity of the group.
            evidence: Administrator evidence to persist.
            actor: Optional audited actor performing the save.

        Returns:
            Whether administrator evidence changed; stale or inapplicable input returns False.
        """
        ...
