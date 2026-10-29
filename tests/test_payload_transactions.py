"""Native payload CAS preserves encrypted data and original retention under concurrent writers."""

import asyncio
import time
from dataclasses import replace

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
