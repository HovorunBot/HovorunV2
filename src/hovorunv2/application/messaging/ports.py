"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from collections.abc import Mapping
from typing import Any, Protocol


class CachePort(Protocol):
    """Protocol for key-value and sorted-set caching operations."""

    async def get(self, key: str, default: Any = None) -> Any:
        """Return decoded data or default for a missing or malformed JSON value."""
        ...

    async def get_text(self, key: str) -> str | None:
        """Return a cached JSON string, or None only when absent; reject malformed values."""
        ...

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        """Store a value in the cache with optional TTL."""
        ...

    async def pop(self, key: str) -> Any:
        """Consume a value atomically; absent keys return None, malformed JSON raises."""
        ...

    async def delete(self, key: str) -> None:
        """Delete a key from the cache."""
        ...

    async def zadd(self, name: str, mapping: Mapping[str, float]) -> None:
        """Add members with scores to a sorted set."""
        ...

    async def zrangebyscore(self, name: str, min_score: float, max_score: float) -> list[str]:
        """Fetch members from sorted set within a score range."""
        ...

    async def zrem(self, name: str, *values: str) -> None:
        """Remove members from a sorted set."""
        ...

    async def zrange(self, name: str, start: int, end: int, *, withscores: bool = False) -> list[Any]:
        """Fetch members from sorted set within an index range."""
        ...
