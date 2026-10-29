"""Native payload CAS preserves encrypted data and original retention under concurrent writers."""

import asyncio
import json
import time
from dataclasses import replace

import pytest
import redis.asyncio as redis
from dishka import AsyncContainer

from hovorunv2.application.feature_jobs import PayloadState
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.bootstrap.worker_runtime import WorkerRuntime
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from tests.utils.runtime import ManualClock, runtime_cache


async def test_competing_payload_cas_keeps_winning_ciphertext_and_original_expiry(
    test_container: AsyncContainer,
) -> None:
    """Async and native writes cannot replace one another or extend the winner's retention."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        bot = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        first = await bot.seal("cas-owner", b"synthetic input", clock.now() + 300)
        second = replace(
            await bot.seal("cas-owner", b"different synthetic input", clock.now() + 300), reference=first.reference
        )
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            results = await asyncio.gather(bot.put(first), asyncio.to_thread(runtime.payloads.put, second))
            assert results.count(PayloadState.READY) == 1
            assert results.count(PayloadState.UNREADABLE) == 1
            winner = first if results[0] == PayloadState.READY else second
            loser = second if winner is first else first
            async with redis.from_url(settings.valkey_url) as client:
                key = namespace.value + "payload:" + first.reference
                original = await client.get(key)
                expiry = await client.pexpiretime(key)
                clock.value += 30
                assert await bot.put(winner) == PayloadState.READY
                assert await asyncio.to_thread(runtime.payloads.put, winner) == PayloadState.READY
                assert await bot.put(loser) == PayloadState.UNREADABLE
                assert await client.get(key) == original == winner.blob.encode()
                assert await client.pexpiretime(key) == expiry


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"job_id": "another-job"}, PayloadState.UNREADABLE),
        ({"deadline": float("nan")}, PayloadState.UNREADABLE),
        ({"deadline": "invalid"}, PayloadState.UNREADABLE),
        ({"salt": 1}, PayloadState.UNREADABLE),
        ({"salt": "invalid-hex"}, PayloadState.UNREADABLE),
        ({"ciphertext": []}, PayloadState.UNREADABLE),
        ({"ciphertext": "invalid-ciphertext"}, PayloadState.UNREADABLE),
        ({"deadline": 1, "salt": 1}, PayloadState.EXPIRED),
    ],
)
async def test_payload_metadata_has_same_safe_outcome_in_both_runtimes(
    test_container: AsyncContainer, changes: dict[str, object], expected: PayloadState
) -> None:
    """Untrusted metadata never escapes as private exceptions or enables mismatched/expired execution."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        bot = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await bot.seal("metadata-owner", b"synthetic input", clock.now() + 300)
        metadata = json.loads(sealed.blob)
        metadata.update(changes)
        key = namespace.value + "payload:" + sealed.reference
        await cache.client.set(key, json.dumps(metadata))
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            assert (await bot.load(sealed.reference, sealed.job_id)).state == expected
            native = await asyncio.to_thread(runtime.payloads.load, sealed.reference, sealed.job_id)
            assert native.state == expected
            assert native.data is None
        assert await cache.client.get(key) == json.dumps(metadata).encode()


@pytest.mark.parametrize("raw", [b"invalid-json", b"\xff", b"null", b"[]"])
async def test_corrupt_payload_is_unreadable_instead_of_missing(test_container: AsyncContainer, raw: bytes) -> None:
    """Existing unusable storage is distinct from a genuinely absent opaque reference."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    reference = "a" * 32
    async with runtime_cache(test_container) as (cache, namespace):
        bot = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        assert (await bot.load(reference, "payload-owner")).state == PayloadState.MISSING
        key = namespace.value + "payload:" + reference
        await cache.client.set(key, raw)
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            assert (await bot.load(reference, "payload-owner")).state == PayloadState.UNREADABLE
            assert (
                await asyncio.to_thread(runtime.payloads.load, reference, "payload-owner")
            ).state == PayloadState.UNREADABLE
        assert await cache.client.get(key) == raw


async def test_native_seal_is_readable_by_async_payload_store(test_container: AsyncContainer) -> None:
    """Native encryption shares original envelope, transient sender label and ciphertext format with the bot."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        bot = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            sealed = await asyncio.to_thread(
                runtime.payloads.seal, "native-owner", b"synthetic input", clock.now() + 300, sender_label="synthetic"
            )
            assert await asyncio.to_thread(runtime.payloads.put, sealed) == PayloadState.READY
            loaded = await bot.load(sealed.reference, sealed.job_id)
            assert loaded.state == PayloadState.READY
            assert loaded.data == b"synthetic input"
            assert loaded.sender_label == "synthetic"
