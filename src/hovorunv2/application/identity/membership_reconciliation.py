"""Bounded refresh of policy-relevant membership evidence."""

import asyncio
import logging
from datetime import UTC, datetime
from weakref import WeakValueDictionary

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import MembershipPort, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.membership import GroupMembershipStateDB

RECONCILIATION_CRON = "* * * * *"
GROUP_BATCH_SIZE = 10
MEMBER_BATCH_SIZE = 50
CYCLE_TIMEOUT_SECONDS = 30
ACTION_TIMEOUT_SECONDS = 15
ACTION_RETRY_SECONDS = 30
logger = logging.getLogger(__name__)


class MembershipReconciliationService:
    """Coordinate startup, explicit and stale-evidence action refreshes by group.

    The per-process lock coalesces startup, cron and explicit refreshes. Stored
    evidence timestamps remain authoritative across processes; no permission
    result is cached here. A failed API check stores UNKNOWN and retains grants.
    """

    def __init__(self, store: MembershipStore, membership: MembershipPort, chats: ChatStore, cache: CachePort) -> None:
        self._store = store
        self._membership = membership
        self._chats = chats
        self._cache = cache
        self._cycle_lock = asyncio.Lock()
        self._refreshing: set[int] = set()
        self._action_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    async def ensure_fresh(self, chat_id: int) -> None:
        """Attempt bounded verification before a protected action with missing or expired snapshots.

        Concurrent actions share a per-group lock and recheck persisted evidence.
        Recent attempts are rate-limited; uncertain evidence never grants access.

        Args:
            chat_id: Telegram identity of the group attempting an action.
        """
        if chat_id >= 0:
            return
        lock = self._action_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._action_locks[chat_id] = lock
        try:
            async with asyncio.timeout(ACTION_TIMEOUT_SECONDS), lock:
                await self._refresh_for_action(chat_id)
        except TimeoutError:
            logger.warning("Action membership verification timed out: chat=%d", chat_id)

    async def _refresh_for_action(self, chat_id: int) -> None:
        """Refresh stale snapshots for an active, present group without repeating recent attempts."""
        group = await self._chats.get_chat(chat_id)
        if group is None or not group.is_bot_present:
            return
        state = await self._store.get_state(group.id)
        now = datetime.now(UTC)
        if state is not None and _snapshots_current(state, now):
            return
        retry_key = f"membership:action_refresh:{group.id}:{group.generation}"
        if await self._cache.get(retry_key):
            logger.info("Action membership verification skipped: chat=%d retry cooldown active", chat_id)
            return
        await self._cache.set(retry_key, 1, expire=ACTION_RETRY_SECONDS)
        logger.info("Refreshing stale membership before action: chat=%d", chat_id)
        await self.refresh_group(group)
        refreshed = await self._store.get_state(group.id)
        if refreshed is not None and _snapshots_current(refreshed, datetime.now(UTC)):
            await self._cache.delete(retry_key)

    async def run_cycle(self, now: datetime | None = None) -> int:
        """Refresh at most ten due groups, each with at most fifty relevant members.

        Args:
            now: Optional current aware datetime; defaults to now in UTC.

        Returns:
            Number of groups refreshed in this cycle, or zero when a cycle is already running.
        """
        if self._cycle_lock.locked():
            logger.info("Membership refresh cycle skipped: cycle already running")
            return 0
        refreshed = 0
        groups: list[ChatDB] = []
        deadline = asyncio.get_running_loop().time() + CYCLE_TIMEOUT_SECONDS
        try:
            async with self._cycle_lock, asyncio.timeout_at(deadline):
                groups = await self._store.due_groups(now or datetime.now(UTC), limit=GROUP_BATCH_SIZE)
                logger.info(
                    "Membership refresh cycle started: due_groups=%d batch_limit=%d", len(groups), GROUP_BATCH_SIZE
                )
                for group in groups:
                    refreshed += await self.refresh_group(group, deadline=deadline)
        except TimeoutError:
            # Unrefreshed evidence stays expired and remains due on the next cycle.
            logger.warning("Membership refresh cycle timed out: refreshed=%d due_groups=%d", refreshed, len(groups))
            return refreshed
        logger.info("Membership refresh cycle completed: refreshed=%d due_groups=%d", refreshed, len(groups))
        return refreshed

    async def refresh_group(self, group: ChatDB, *, deadline: float | None = None) -> bool:
        """Refresh bot, administrator and member evidence for a single group generation.

        Args:
            group: Group to refresh.
            deadline: Optional absolute event-loop deadline; defaults to a fresh timeout.

        Returns:
            Whether the group was refreshed; False when a refresh is already in progress.
        """
        if group.id in self._refreshing:
            logger.info("Membership refresh skipped: group=%d already running", group.id)
            return False
        self._refreshing.add(group.id)
        deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + CYCLE_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout_at(deadline):
                bot = await self._membership.verify_bot(group.chat_id)
            await self._store.save_bot(group.id, bot)
            logger.info("Bot membership refreshed: chat=%d status=%s", group.chat_id, bot.membership.status)
            async with asyncio.timeout_at(deadline):
                administrators = await self._membership.list_administrators(group.chat_id)
            await self._store.save_admins(group.id, administrators)
            logger.info(
                "Administrator snapshot refreshed: chat=%d known=%s administrators=%d expires_at=%.0f",
                group.chat_id,
                administrators.known,
                len(administrators.members),
                administrators.expires_at.timestamp(),
            )
            users = await self._store.relevant_users(group.id, limit=MEMBER_BATCH_SIZE)
            for user in users:
                async with asyncio.timeout_at(deadline):
                    evidence = await self._membership.verify_member(group.chat_id, user.telegram_id)
                await self._store.save_member(group.id, evidence, expected_user_id=user.id)
        finally:
            self._refreshing.discard(group.id)
        logger.info(
            "Membership refreshed: group=%d administrators=%d members=%d",
            group.id,
            len(administrators.members),
            len(users),
        )
        return True


def _snapshot_current(verified_at: datetime | None, expires_at: datetime | None, now: datetime) -> bool:
    """Return whether a persisted verification attempt still covers the current time."""
    if verified_at is None or expires_at is None:
        return False
    return verified_at <= now < expires_at


def _snapshots_current(state: GroupMembershipStateDB, now: datetime) -> bool:
    """Require known bot and administrator snapshots within their verification windows."""
    if not state.admins_known or state.bot_status == MembershipStatus.UNKNOWN:
        return False
    return _snapshot_current(state.admins_verified_at, state.admins_expires_at, now) and _snapshot_current(
        state.bot_verified_at, state.bot_expires_at, now
    )
