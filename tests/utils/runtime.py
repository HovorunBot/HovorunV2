"""Isolated real Valkey namespaces and controlled clocks for runtime integration tests."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

import redis.asyncio as redis
from dishka import AsyncContainer

from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings


@dataclass
class ManualClock:
    """Advance quota time directly without sleeps or replacing internal services."""

    value: float = 120.0

    def now(self) -> float:
        """Return the controlled Unix timestamp."""
        return self.value


@asynccontextmanager
async def runtime_cache(container: AsyncContainer) -> AsyncIterator[tuple[CacheService, RuntimeNamespace]]:
    """Use only a fresh random test namespace; never clear another test or application's keys."""
    settings = await container.get(Settings)
    namespace = RuntimeNamespace(f"test:{{{uuid4().hex}}}:")
    cache = CacheService(settings.valkey_url)
    client = redis.from_url(settings.valkey_url)
    try:
        await client.ping()
        yield cache, namespace
    finally:
        try:
            keys = [key async for key in client.scan_iter(match="*" + namespace.value + "*")]
            if keys:
                await client.delete(*keys)
        finally:
            await client.aclose()
            await cache.close()
