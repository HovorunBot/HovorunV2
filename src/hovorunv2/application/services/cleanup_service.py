"""Service for handling persistent message cleanup using Valkey."""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from aiogram import Bot

logger = get_logger(__name__)


class CleanupService:
    """Service to manage message self-destruction based on Valkey TTL."""

    # Redis keys
    CLEANUP_INDEX = "cleanup:index"  # ZSET of {chat_id}:{message_id} -> timestamp
    CLEANUP_SINGLETON_PREFIX = "cleanup:singleton"  # STRING of {chat_id}:{type} -> message_id

    # Default interval to check when nothing is pending
    IDLE_CHECK_INTERVAL: int = 3600
    # Minimum sleep interval to prevent busy loops
    MIN_CHECK_INTERVAL: int = 5

    def __init__(self, cache_service: CacheService) -> None:
        """Initialize service with cache dependency."""
        self._cache = cache_service
        self._loop_task: asyncio.Task[None] | None = None
        self._trigger = asyncio.Event()

    def _make_member(self, chat_id: int, message_id: int) -> str:
        """Format ZSET member string."""
        return f"{chat_id}:{message_id}"

    def _parse_member(self, member: str) -> tuple[int, int]:
        """Parse ZSET member string."""
        parts = member.split(":")
        return int(parts[0]), int(parts[1])

    def _make_singleton_key(self, chat_id: int, cleanup_type: str) -> str:
        """Format singleton key."""
        return f"{self.CLEANUP_SINGLETON_PREFIX}:{chat_id}:{cleanup_type}"

    async def register_window(self, chat_id: int, message_id: int, ttl: int, cleanup_type: str | None = None) -> None:
        """Register a message for self-destruction after inactivity."""
        expires_at = datetime.now(UTC).timestamp() + ttl
        member = self._make_member(chat_id, message_id)

        await self._cache.zadd(self.CLEANUP_INDEX, {member: expires_at})

        if cleanup_type:
            singleton_key = self._make_singleton_key(chat_id, cleanup_type)
            await self._cache.set(singleton_key, message_id, expire=ttl + 3600)  # Buffer for singleton

        self._trigger.set()

    async def reset_ttl(self, chat_id: int, message_id: int, ttl: int, cleanup_type: str | None = None) -> None:
        """Reset the inactivity timer for a registered message."""
        await self.register_window(chat_id, message_id, ttl, cleanup_type)

    async def unregister_window(self, chat_id: int, message_id: int) -> None:
        """Manually unregister a window from cleanup."""
        member = self._make_member(chat_id, message_id)
        await self._cache.zrem(self.CLEANUP_INDEX, member)

    async def replace_singleton_window(
        self, bot: Bot, chat_id: int, message_id: int, ttl: int, cleanup_type: str
    ) -> None:
        """Replace any existing window of the same type in the chat with a new one."""
        singleton_key = self._make_singleton_key(chat_id, cleanup_type)
        existing_msg_id = await self._cache.get(singleton_key)

        if existing_msg_id:
            with contextlib.suppress(Exception):
                await bot.delete_message(chat_id=chat_id, message_id=int(existing_msg_id))
            await self.unregister_window(chat_id, int(existing_msg_id))

        await self.register_window(chat_id, message_id, ttl, cleanup_type)

    async def start_cleanup_loop(self, bot: Bot) -> None:
        """Start the background loop to process expired messages."""
        if self._loop_task and not self._loop_task.done():
            return

        self._loop_task = asyncio.create_task(self._run_loop(bot))
        logger.info("Valkey cleanup loop started")

    async def _run_loop(self, bot: Bot) -> None:
        """Background loop that periodically checks and deletes expired messages."""
        while True:
            try:
                now = datetime.now(UTC).timestamp()

                # 1. Process all currently expired messages
                expired_members = await self._cache.zrangebyscore(self.CLEANUP_INDEX, 0, now)

                for member in expired_members:
                    chat_id, message_id = self._parse_member(member)
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=message_id)
                        logger.info("Successfully removed expired message %d in chat %d", message_id, chat_id)
                    except Exception as e:
                        logger.warning("Could not delete message %d in chat %d: %s", message_id, chat_id, e)

                if expired_members:
                    await self._cache.zrem(self.CLEANUP_INDEX, *expired_members)

                # 2. Determine how long to sleep
                next_expiry_list = await self._cache.zrange(self.CLEANUP_INDEX, 0, 0, withscores=True)

                if next_expiry_list:
                    _, next_expiry = next_expiry_list[0]
                    sleep_time = next_expiry - now
                    sleep_time = max(self.MIN_CHECK_INTERVAL, sleep_time)
                else:
                    sleep_time = self.IDLE_CHECK_INTERVAL

                # 3. Wait for the calculated time OR a new registration trigger
                try:
                    await asyncio.wait_for(self._trigger.wait(), timeout=sleep_time)
                    self._trigger.clear()
                except TimeoutError:
                    pass

            except Exception:
                logger.exception("Error in Valkey cleanup loop")
                await asyncio.sleep(60)
