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

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        """Store a value in the cache with an optional expiration time in seconds."""
        logger.debug("Caching key: %s (expire: %s)", key, expire)

        # Convert to JSON
        payload = json.dumps(value)
        await self._cache.set(key, payload, ex=expire)

    async def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from the cache by key."""
        raw_value = await self._cache.get(key)
        if raw_value is None:
            logger.debug("Cache lookup for key: %s (found: False)", key)
            return default

        try:
            # Redis-py async get() returns bytes or str.
            data = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else raw_value
            value = json.loads(data)
            logger.debug("Cache lookup for key: %s (found: True)", key)
        except json.JSONDecodeError:
            logger.exception("Failed to decode cache value for key %s", key)
            return default
        else:
            return value

    async def delete(self, key: str) -> None:
        """Delete a key from the cache."""
        await self._cache.delete(key)

    async def zadd(self, name: str, mapping: dict[str, float]) -> None:
        """Add members to a sorted set."""
        await self._cache.zadd(name, mapping)

    async def zrangebyscore(self, name: str, min_score: float, max_score: float) -> list[str]:
        """Return a range of members in a sorted set by their score."""
        results = await self._cache.zrangebyscore(name, min_score, max_score)
        return [r.decode("utf-8") if isinstance(r, bytes) else r for r in results]

    async def zrem(self, name: str, *values: str) -> None:
        """Remove members from a sorted set."""
        await self._cache.zrem(name, *values)

    async def zrange(self, name: str, start: int, end: int, *, withscores: bool = False) -> list[Any]:
        """Return a range of members in a sorted set by their index."""
        results = await self._cache.zrange(name, start, end, withscores=withscores)
        if withscores:
            return [(r[0].decode("utf-8") if isinstance(r[0], bytes) else r[0], r[1]) for r in results]
        return [r.decode("utf-8") if isinstance(r, bytes) else r for r in results]

    async def close(self) -> None:
        """Close cache connection."""
        await self._cache.aclose()
