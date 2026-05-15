"""Tests for CacheService persistent encryption."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from hovorunv2.infrastructure.cache import CacheService


@pytest.mark.asyncio
async def test_cache_encryption_roundtrip() -> None:
    """Test that data is encrypted and correctly decrypted with persistent master key."""
    master_key = Fernet.generate_key().decode()

    with patch("redis.asyncio.from_url") as mock_redis_from_url:
        mock_redis = mock_redis_from_url.return_value
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock()

        service = CacheService()
        service.setup_encryption(master_key)

        test_key = "test_persist"
        test_data = {"secret": "data"}

        # 1. Test SET (should encrypt)
        stored_value = None

        async def mock_set(_k: str, v: str, **_kwargs: Any) -> None:
            nonlocal stored_value
            stored_value = v

        mock_redis.set.side_effect = mock_set

        await service.set(test_key, test_data)

        # Verify it's encrypted
        assert "secret" not in str(stored_value)

        # 2. Test GET (should decrypt)
        mock_redis.get.return_value = stored_value
        retrieved = await service.get(test_key)
        assert retrieved == test_data


@pytest.mark.asyncio
async def test_cache_persistence_across_restarts() -> None:
    """Test that data remains readable if the same master key is provided after restart."""
    master_key = Fernet.generate_key().decode()
    test_data = {"persistent": "value"}

    with patch("redis.asyncio.from_url") as mock_redis_from_url:
        mock_redis = mock_redis_from_url.return_value
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock()

        # Instance 1: Write data
        service1 = CacheService()
        service1.setup_encryption(master_key)

        stored_value = None

        async def mock_set(_k: str, v: str, **_kwargs: Any) -> None:
            nonlocal stored_value
            stored_value = v

        mock_redis.set.side_effect = mock_set

        await service1.set("key", test_data)

        # Instance 2: Read data (restart simulation)
        service2 = CacheService()
        service2.setup_encryption(master_key)

        mock_redis.get.return_value = stored_value
        assert await service2.get("key") == test_data


@pytest.mark.asyncio
async def test_cache_invalid_key_on_restart() -> None:
    """Test that data is unreadable if a different master key is provided."""
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    test_data = {"sensitive": "info"}

    with patch("redis.asyncio.from_url") as mock_redis_from_url:
        mock_redis = mock_redis_from_url.return_value
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock()

        # Instance 1: Write with Key 1
        service1 = CacheService()
        service1.setup_encryption(key1)

        stored_value = None

        async def mock_set(_k: str, v: str, **_kwargs: Any) -> None:
            nonlocal stored_value
            stored_value = v

        mock_redis.set.side_effect = mock_set
        await service1.set("key", test_data)

        # Instance 2: Read with Key 2
        service2 = CacheService()
        service2.setup_encryption(key2)

        mock_redis.get.return_value = stored_value
        # Should fail to decrypt and return None
        assert await service2.get("key") is None
