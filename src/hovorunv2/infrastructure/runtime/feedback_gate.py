"""Valkey-backed per-target delayed feedback coalescing with a bounded fallback."""

import time
from collections.abc import Callable

import redis

from hovorunv2.application.jobs.http_ports import SyncFeedbackGate
from hovorunv2.application.usage.budgets import RuntimeNamespace

MAX_LOCAL_ENTRIES = 1024


class ValkeyFeedbackGate(SyncFeedbackGate):
    """Claim at most one delayed notice per target and namespace."""

    def __init__(
        self,
        client: redis.Redis,
        namespace: RuntimeNamespace,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with a synchronous Valkey client and bounded local fallback."""
        self._client = client
        self._namespace = namespace
        self._clock = clock
        self._local: dict[str, float] = {}

    def claim_delayed(self, target_key: str, ttl: int) -> bool:
        """Claim one delayed notice for the target within the ttl window."""
        key = f"{self._namespace.value}feedback:delayed:{target_key}"
        try:
            return bool(self._client.set(key, "1", nx=True, ex=ttl))
        except redis.RedisError, OSError:
            return self._local_claim(target_key, ttl)

    def _local_claim(self, target_key: str, ttl: int) -> bool:
        now = self._clock()
        last = self._local.get(target_key)
        if last is not None and now - last < ttl:
            return False
        if len(self._local) >= MAX_LOCAL_ENTRIES:
            oldest = min(self._local, key=self._local.__getitem__)
            del self._local[oldest]
        self._local[target_key] = now
        return True
