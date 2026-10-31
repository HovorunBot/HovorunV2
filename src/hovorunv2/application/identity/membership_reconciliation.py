"""Bounded refresh of policy-relevant membership evidence."""

import asyncio
from datetime import UTC, datetime

from hovorunv2.application.identity.membership import MembershipPort
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.domain.chat import ChatDB

RECONCILIATION_CRON = "* * * * *"
GROUP_BATCH_SIZE = 10
MEMBER_BATCH_SIZE = 50
CYCLE_TIMEOUT_SECONDS = 30


class MembershipReconciliationService:
    """Coordinate refresh by group without any ordinary-message API calls.

    The per-process lock coalesces startup, cron and explicit refreshes. Stored
    evidence timestamps remain authoritative across processes; no permission
    result is cached here. A failed API check stores UNKNOWN and retains grants.
    """

    def __init__(self, store: MembershipStore, membership: MembershipPort) -> None:
        self._store = store
        self._membership = membership
        self._cycle_lock = asyncio.Lock()
        self._refreshing: set[int] = set()

    async def run_cycle(self, now: datetime | None = None) -> int:
        """Refresh at most ten groups, each with at most fifty relevant members."""
        if self._cycle_lock.locked():
            return 0
        async with self._cycle_lock:
            refreshed = 0
            deadline = asyncio.get_running_loop().time() + CYCLE_TIMEOUT_SECONDS
            groups = await self._store.due_groups(now or datetime.now(UTC), limit=GROUP_BATCH_SIZE)
            try:
                for group in groups:
                    refreshed += await self.refresh_group(group, deadline=deadline)
            except TimeoutError:
                # Unrefreshed evidence stays expired and remains due on the next cycle.
                return refreshed
            return refreshed

    async def refresh_group(self, group: ChatDB, *, deadline: float | None = None) -> bool:
        """Coalesce simultaneous requests for the same active group generation."""
        if group.id in self._refreshing:
            return False
        self._refreshing.add(group.id)
        deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + CYCLE_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout_at(deadline):
                bot = await self._membership.verify_bot(group.chat_id)
            await self._store.save_bot(group.id, bot)
            async with asyncio.timeout_at(deadline):
                administrators = await self._membership.list_administrators(group.chat_id)
            await self._store.save_admins(group.id, administrators)
            users = await self._store.relevant_users(group.id, limit=MEMBER_BATCH_SIZE)
            for user in users:
                async with asyncio.timeout_at(deadline):
                    evidence = await self._membership.verify_member(group.chat_id, user.telegram_id)
                await self._store.save_member(group.id, evidence, expected_user_id=user.id)
        finally:
            self._refreshing.discard(group.id)
        return True
