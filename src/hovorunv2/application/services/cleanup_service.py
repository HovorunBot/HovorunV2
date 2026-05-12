"""Service for handling delayed message cleanup with inactivity tracking."""

import asyncio
from typing import TYPE_CHECKING

from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from aiogram import Bot

logger = get_logger(__name__)


class CleanupService:
    """Service to manage message self-destruction based on inactivity TTL."""

    def __init__(self, cache_service: CacheService) -> None:
        """Initialize service with cache dependencies."""
        self._cache = cache_service
        self._tasks: set[asyncio.Task[None]] = set()

    async def register_message(self, bot: Bot, chat_id: int, message_id: int, ttl: int) -> None:
        """Register a message for self-destruction after inactivity.

        Args:
            bot: The bot instance to perform deletion.
            chat_id: Telegram chat ID.
            message_id: Telegram message ID.
            ttl: Time-to-live in seconds.

        """
        cache_key = self._get_cache_key(chat_id, message_id)
        await self._cache.set(cache_key, "active", expire=ttl)

        task = asyncio.create_task(self._cleanup_task(bot, chat_id, message_id, cache_key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def reset_ttl(self, chat_id: int, message_id: int, ttl: int) -> None:
        """Reset the inactivity timer for a registered message."""
        cache_key = self._get_cache_key(chat_id, message_id)
        await self._cache.set(cache_key, "active", expire=ttl)

    async def _cleanup_task(self, bot: Bot, chat_id: int, message_id: int, cache_key: str) -> None:
        """Background task that waits for TTL expiry and deletes the message."""
        while True:
            await asyncio.sleep(5)  # Check every 5 seconds
            if not await self._cache.get(cache_key):
                break

        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            logger.info("Successfully removed expired message %d in chat %d", message_id, chat_id)
        except Exception:
            logger.exception("Failed to delete expired message %d in chat %d", message_id, chat_id)

    def _get_cache_key(self, chat_id: int, message_id: int) -> str:
        """Generate internal cache key for cleanup tracking."""
        return f"cleanup:{chat_id}:{message_id}"
