"""Service for handling persistent message cleanup using Valkey."""

import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from hovorunv2.application.messaging.ports import CachePort

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


class CleanupService:
    """Service to manage message self-destruction based on Valkey TTL."""

    CLEANUP_INDEX = "cleanup:index"
    CLEANUP_SINGLETON_PREFIX = "cleanup:singleton"

    def __init__(self, cache_service: CachePort) -> None:
        """Initialize service with cache dependency."""
        self._cache = cache_service

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
            await self._cache.set(singleton_key, message_id, expire=ttl + 3600)

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

    async def process_expired(self, bot: Bot, now: float | None = None) -> int:
        """Attempt each due deletion and remove the processed batch from the index."""
        current_time = now if now is not None else datetime.now(UTC).timestamp()
        expired_members = await self._cache.zrangebyscore(self.CLEANUP_INDEX, 0, current_time)
        deleted_count = 0
        for member in expired_members:
            if await self._delete_single_expired(bot, member):
                deleted_count += 1
        if expired_members:
            await self._cache.zrem(self.CLEANUP_INDEX, *expired_members)
        return deleted_count

    async def _delete_single_expired(self, bot: Bot, member: str) -> bool:
        chat_id, message_id = self._parse_member(member)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as error:
            logger.warning("Could not delete message %d in chat %d (%s)", message_id, chat_id, type(error))
            return False
        else:
            logger.info("Successfully removed expired message %d in chat %d", message_id, chat_id)
            return True
