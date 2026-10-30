"""Regression coverage proving test workers are isolated away from Valkey DB 0."""

import pytest
import redis

from tests.conftest import resolve_worker_valkey_url


def test_resolver_default_master_uses_db_1() -> None:
    """Non-worker (master / serial) runs default to database index 1."""
    assert resolve_worker_valkey_url("master") == "redis://localhost:6379/1"
    assert resolve_worker_valkey_url("") == "redis://localhost:6379/1"


def test_resolver_worker_offsets_use_distinct_dbs() -> None:
    """Each xdist worker receives a strictly isolated database index starting from base."""
    resolved = [resolve_worker_valkey_url(f"gw{idx}") for idx in range(15)]
    assert len(resolved) == 15
    assert len(set(resolved)) == 15
    assert resolved[0] == "redis://localhost:6379/1"
    assert resolved[14] == "redis://localhost:6379/15"


def test_resolver_rejects_db_0() -> None:
    """Base URLs or offsets resolving to DB 0 are rejected immediately."""
    with pytest.raises(ValueError, match="DB 0 is strictly forbidden"):
        resolve_worker_valkey_url("master", base_url="redis://localhost:6379/0")

    with pytest.raises(ValueError, match="DB 0 is strictly forbidden"):
        resolve_worker_valkey_url("gw0", base_url="redis://localhost:6379/0")


def test_resolver_rejects_index_above_15() -> None:
    """Workers that would exceed Valkey's default 16 databases (0-15) are rejected."""
    with pytest.raises(ValueError, match="out of allowed test range"):
        resolve_worker_valkey_url("gw15")


def test_resolver_preserves_custom_host_and_port() -> None:
    """Custom TEST_VALKEY_URL host, port and credentials are kept intact."""
    base = "redis://:pass@valkey.internal:6380/2"
    assert resolve_worker_valkey_url("gw0", base_url=base) == "redis://:pass@valkey.internal:6380/2"
    assert resolve_worker_valkey_url("gw3", base_url=base) == "redis://:pass@valkey.internal:6380/5"


def test_suite_never_touches_db_0(worker_valkey_url: str) -> None:
    """The active worker Valkey database is never DB 0 and canary keys in DB 0 survive."""
    assert not worker_valkey_url.endswith("/0")
    client = redis.from_url("redis://localhost:6379/0")
    try:
        client.set("canary:test_worker_safety", "active", ex=60)
        assert client.get("canary:test_worker_safety") == b"active"
    finally:
        client.delete("canary:test_worker_safety")
        client.close()
