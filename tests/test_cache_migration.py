"""Tests for CacheService encryption migration."""

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from hovorunv2.infrastructure.cache import CacheService


@pytest.mark.asyncio
async def test_cache_migration() -> None:
    """Test migration of unencrypted data to encrypted data."""
    master_key = Fernet.generate_key().decode()
    fernet = Fernet(master_key.encode())

    with patch("redis.asyncio.from_url") as mock_redis_from_url:
        mock_redis = mock_redis_from_url.return_value

        # In-memory mock storage for redis
        storage: dict[str, Any] = {}
        ttls: dict[str, int] = {}

        async def mock_set(k: str, v: Any, ex: int | None = None, **_kwargs: Any) -> None:
            storage[k] = v
            if ex:
                ttls[k] = ex
            else:
                ttls[k] = -1

        async def mock_get(k: str) -> Any:
            return storage.get(k)

        async def mock_ttl(k: str) -> int:
            return ttls.get(k, -2)

        async def mock_scan(cursor: int = 0, match: str = "*", count: int | None = None) -> tuple[int, list[bytes]]:
            # Just return everything in one go for the test
            if cursor != 0:
                return 0, []
            keys = [k.encode() if isinstance(k, str) else k for k in storage]
            return 0, keys

        mock_redis.set.side_effect = mock_set
        mock_redis.get.side_effect = mock_get
        mock_redis.ttl.side_effect = mock_ttl
        mock_redis.scan.side_effect = mock_scan

        service = CacheService()

        # 1. Store unencrypted data
        unencrypted_key = "msg:123:456"
        unencrypted_data = {"text": "hello world"}
        # value in CacheService.set is first json.dumps-ed
        await service.set(unencrypted_key, unencrypted_data, expire=3600)

        # Verify it's actually unencrypted in storage (as double-JSON encoded string)
        raw_stored = storage[unencrypted_key]
        assert json.loads(raw_stored) == unencrypted_data
        assert ttls[unencrypted_key] == 3600

        # 2. Setup encryption and run migration
        service.setup_encryption(master_key)
        await service.migrate_to_encryption()

        # 3. Verify data is now encrypted
        raw_migrated = storage[unencrypted_key]
        # Decrypt manually to verify
        decrypted = fernet.decrypt(raw_migrated.encode() if isinstance(raw_migrated, str) else raw_migrated)
        assert json.loads(decrypted) == unencrypted_data
        # TTL should be preserved (roughly)
        assert ttls[unencrypted_key] == 3600

        # 4. Verify we can still get it via service
        retrieved = await service.get(unencrypted_key)
        assert retrieved == unencrypted_data

        # 5. Verify already encrypted data is not double-encrypted
        already_encrypted_key = "msg:789:012"
        already_encrypted_data = {"text": "already secret"}
        await service.set(already_encrypted_key, already_encrypted_data)

        storage_before = storage[already_encrypted_key]
        await service.migrate_to_encryption()
        storage_after = storage[already_encrypted_key]

        assert storage_before == storage_after
        assert await service.get(already_encrypted_key) == already_encrypted_data


@pytest.mark.asyncio
async def test_cache_migration_skip_invalid_json() -> None:
    """Test that migration skips keys that don't contain valid JSON."""
    master_key = Fernet.generate_key().decode()

    with patch("redis.asyncio.from_url") as mock_redis_from_url:
        mock_redis = mock_redis_from_url.return_value
        storage = {"binary_data": b"\xff\xfe\xfd"}

        async def mock_get(k: str) -> Any:
            return storage.get(k)

        async def mock_scan(cursor: int = 0, **_kwargs: Any) -> tuple[int, list[bytes]]:
            if cursor != 0:
                return 0, []
            return 0, [b"binary_data"]

        mock_redis.get.side_effect = mock_get
        mock_redis.scan.side_effect = mock_scan
        mock_redis.set = AsyncMock()

        service = CacheService()
        service.setup_encryption(master_key)
        await service.migrate_to_encryption()

        # Should not have called set for binary data
        mock_redis.set.assert_not_called()
