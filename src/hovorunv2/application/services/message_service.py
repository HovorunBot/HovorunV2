"""Message service for handling Telegram messages and caching."""

import json
from typing import TYPE_CHECKING

from aiogram import types

from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from hovorunv2.infrastructure.cache import CacheService

logger = get_logger(__name__)


class MessageService:
    """Service for handling and caching Telegram messages."""

    CACHE_TTL_SECONDS: int = 60 * 60 * 24  # 24 hours

    def __init__(self, cache_service: CacheService) -> None:
        """Initialize message service with a cache service."""
        self.cache = cache_service
        self.ttl = self.CACHE_TTL_SECONDS
        logger.debug("MessageService initialized with TTL: %d seconds", self.ttl)

    async def cache_message(self, message: types.Message) -> None:
        """Cache a Telegram message with all available information."""
        key = self._generate_key(message.chat.id, message.message_id)
        logger.debug("Caching message %d from chat %d", message.message_id, message.chat.id)

        try:
            # aiogram objects have a bot reference and internal defaults.
            # We export only the data fields.
            json_data = message.model_dump_json(exclude_defaults=True)
            await self.cache.set(key, json_data, expire=self.ttl)
        except Exception:
            logger.exception("Failed to serialize message")
            # Fallback: manual dict conversion if model_dump_json fails
            data_dict = message.model_dump(exclude_none=True)
            await self.cache.set(key, json.dumps(data_dict), expire=self.ttl)

    async def get_message(self, chat_id: int, message_id: int) -> types.Message | None:
        """Retrieve a cached message by chat ID and message ID."""
        key = self._generate_key(chat_id, message_id)
        data = await self.cache.get(key)
        if data:
            return types.Message.model_validate_json(data)
        return None

    def _generate_key(self, chat_id: int, message_id: int) -> str:
        """Generate a cache key for a specific message."""
        return f"msg:{chat_id}:{message_id}"
