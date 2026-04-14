"""Module for application caching."""

from typing import Any

from diskcache_rs import Cache

from logger_conf import get_logger

logger = get_logger(__name__)


class CacheService:
    """Application cache service.

    Used for keeping application data in memory and on disk temporarily.
    Only communicates with diskcache_rs and handles low-level data structures.

    See https://github.com/loonghao/diskcache_rs for more details.
    """

    def __init__(self) -> None:
        """Initialize cache service."""
        logger.info("Initializing CacheService")
        self._cache = Cache()  # Follow default configuration for now, will update it later

    def set(self, key: str, value: Any, expire: int | None = None) -> None:  # noqa: ANN401
        """Store a value in the cache with an optional expiration time in seconds."""
        logger.debug("Caching key: %s (expire: %s)", key, expire)
        if expire is not None:
            self._cache.set(key, value, expire=expire)
        else:
            self._cache.set(key, value)

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Retrieve a value from the cache by key."""
        value = self._cache.get(key, default)
        logger.debug("Cache lookup for key: %s (found: %s)", key, value is not None)
        return value
