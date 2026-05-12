"""Module for application caching."""

import json
from typing import Any

import redis.asyncio as redis
from cryptography.fernet import Fernet

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class CacheService:
    """Application cache service.

    Used for keeping application data in memory and on disk temporarily.
    Only communicates with Valkey and handles low-level data structures.
    Supports persistent application-level encryption (AES) with key wrapping.
    """

    def __init__(self) -> None:
        """Initialize cache service."""
        logger.info("Initializing CacheService with Valkey at %s", settings.valkey_url)
        self._cache = redis.from_url(settings.valkey_url)
        self._master_fernet = None

    def setup_encryption(self, master_key: str) -> None:
        """Initialize encryption using a persistent master key.

        Args:
            master_key: The plaintext Fernet key to use for cache encryption.

        """
        try:
            self._master_fernet = Fernet(master_key.encode())
            logger.info("Persistent cache encryption enabled.")
        except Exception:
            logger.exception("Failed to initialize cache encryption with provided key.")

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:  # noqa: ANN401
        """Store a value in the cache with an optional expiration time in seconds."""
        logger.debug("Caching key: %s (expire: %s)", key, expire)

        # Convert to JSON
        payload = json.dumps(value).encode()

        # Apply persistent encryption if enabled
        if self._master_fernet:
            payload = self._master_fernet.encrypt(payload)

        # Ensure payload is string for redis storage
        final_value = payload.decode() if isinstance(payload, bytes) else payload
        await self._cache.set(key, final_value, ex=expire)

    async def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Retrieve a value from the cache by key."""
        raw_value = await self._cache.get(key)
        if raw_value is None:
            logger.debug("Cache lookup for key: %s (found: False)", key)
            return default

        try:
            # Redis-py async get() returns bytes or str.
            data = raw_value if isinstance(raw_value, bytes) else raw_value.encode("utf-8")

            # Decrypt if persistent encryption is enabled
            if self._master_fernet:
                try:
                    data = self._master_fernet.decrypt(data)
                except Exception:
                    logger.warning("Failed to decrypt cache key %s. Key might have changed.", key)
                    return default

            value = json.loads(data.decode("utf-8"))
            logger.debug("Cache lookup for key: %s (found: True)", key)
        except json.JSONDecodeError, UnicodeDecodeError:
            logger.exception("Failed to decode cache value for key %s", key)
            return default
        else:
            return value

    async def close(self) -> None:
        """Close cache connection."""
        await self._cache.aclose()

    async def migrate_to_encryption(self) -> None:
        """One-time migration: encrypt all currently unencrypted keys in cache.

        This is a temporal workaround for data cached before encryption was implemented.
        It iterates over all keys, checks if they are encrypted, and if not,
        tries to encrypt them if they contain valid JSON.
        """
        if not self._master_fernet:
            logger.debug("Skipping cache migration: encryption not enabled.")
            return

        logger.info("Starting cache encryption migration...")
        cursor = 0
        migrated_count = 0

        try:
            while True:
                cursor, keys = await self._cache.scan(cursor=cursor, match="*")
                for key_bytes in keys:
                    key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else key_bytes
                    raw_value = await self._cache.get(key)
                    if raw_value is None:
                        continue

                    # Ensure data is bytes for Fernet
                    data = raw_value if isinstance(raw_value, bytes) else raw_value.encode("utf-8")

                    # 1. Check if already encrypted with current master key
                    try:
                        self._master_fernet.decrypt(data)
                        # Already encrypted, skip
                        continue
                    except Exception:
                        # Not encrypted or encrypted with different key
                        pass

                    # 2. Try to see if it's unencrypted JSON
                    try:
                        decoded = data.decode("utf-8")
                        json.loads(decoded)

                        # Valid JSON and not encrypted -> Encrypt it
                        encrypted_payload = self._master_fernet.encrypt(data)
                        final_value = encrypted_payload.decode()

                        # Preserve TTL
                        ttl = await self._cache.ttl(key)
                        if ttl > 0:
                            await self._cache.set(key, final_value, ex=ttl)
                        elif ttl == -1:  # No expiration
                            await self._cache.set(key, final_value)

                        migrated_count += 1
                        logger.debug("Migrated key to encryption: %s", key)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        # Not valid JSON, skip (might be binary data or something else)
                        continue

                if cursor == 0:
                    break
        except Exception:
            logger.exception("Error during cache encryption migration")

        if migrated_count > 0:
            logger.info("Cache encryption migration finished. Migrated %d keys.", migrated_count)
        else:
            logger.info("Cache encryption migration finished. No keys needed migration.")
