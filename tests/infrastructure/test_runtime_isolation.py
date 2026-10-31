"""Runtime test helpers remove only their isolated Valkey namespace."""

import redis.asyncio as redis
from dishka import AsyncContainer

from hovorunv2.infrastructure.config import Settings
from tests.utils.runtime import runtime_cache


async def test_runtime_cache_removes_isolated_namespace_keys(test_container: AsyncContainer) -> None:
    """Leaving the runtime context deletes every key in its random namespace."""
    async with runtime_cache(test_container) as (cache, namespace):
        key = namespace.value + "probe"
        await cache.set(key, "value", expire=60)
        assert await cache.get(key) == "value"

    settings = await test_container.get(Settings)
    client = redis.from_url(settings.valkey_url)
    try:
        remaining = [key async for key in client.scan_iter(match="*" + namespace.value + "*")]
    finally:
        await client.aclose()
    assert remaining == []
