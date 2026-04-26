"""Module for application caching."""

import json
from typing import Any

import redis.asyncio as redis

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class CacheService:
    """Application cache service.

    Used for keeping application data in memory and on disk temporarily.
    Only communicates with Valkey and handles low-level data structures.
    """

    def __init__(self) -> None:
        """Initialize cache service."""
        logger.info("Initializing CacheService with Valkey at %s", settings.valkey_url)
        self._cache = redis.from_url(settings.valkey_url)

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:  # noqa: ANN401
        """Store a value in the cache with an optional expiration time in seconds."""
        logger.debug("Caching key: %s (expire: %s)", key, expire)
        json_value = json.dumps(value)
        await self._cache.set(key, json_value, ex=expire)

    async def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Retrieve a value from the cache by key."""
        raw_value = await self._cache.get(key)
        if raw_value is None:
            logger.debug("Cache lookup for key: %s (found: False)", key)
            return default

        try:
            # Redis-py async get() returns bytes or str.
            if isinstance(raw_value, bytes):
                raw_value = raw_value.decode("utf-8")

            if not isinstance(raw_value, str):
                logger.error("Unexpected cache value type for key %s: %s", key, type(raw_value))
                return default

            value = json.loads(raw_value)
            logger.debug("Cache lookup for key: %s (found: True)", key)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("Failed to decode cache value for key %s", key)
            return default
        else:
            return value

    async def close(self) -> None:
        """Close cache connection."""
        await self._cache.aclose()
