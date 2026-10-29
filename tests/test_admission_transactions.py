"""Real Valkey failure boundaries for admission WATCH transactions in both runtimes."""

import asyncio
import json
import secrets
import time
from dataclasses import replace

import pytest
from dishka import AsyncContainer

from hovorunv2.application.admission import AdmissionLimits, AdmissionState, JobStage, TicketState
from hovorunv2.application.feature_jobs import EnqueueState, JobOutcome, PayloadState
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.bootstrap.worker_runtime import WorkerRuntime
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.admission_state import admission_keys
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from tests.test_feature_jobs import PRIVATE_BODY, job_envelope
from tests.utils.runtime import ManualClock, runtime_cache


async def test_concurrent_same_job_admission_returns_original_ticket(test_container: AsyncContainer) -> None:
    """An intervening commit between dedupe and identity reads retries instead of reporting a false conflict."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("same-job-replay", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        results = await asyncio.gather(
            *(store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now()) for _attempt in range(20))
        )
        assert sum(result.state == AdmissionState.ACCEPTED for result in results) == 1
        assert sum(result.state == AdmissionState.DUPLICATE for result in results) == 19
        assert all(result.ticket is not None and result.ticket.envelope == envelope for result in results)
        keys = admission_keys(namespace.value, envelope)
        assert await cache.client.hget(keys.totals, "count") == b"1"
        assert await cache.client.hget(keys.totals, "bytes") == str(sealed.byte_size).encode()


async def test_nontext_dedupe_pointer_fails_closed_without_mutation(test_container: AsyncContainer) -> None:
    """Undecodable stored pointers never escape as codec errors or reset admission capacity."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("invalid-dedupe-bytes", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        keys = admission_keys(namespace.value, envelope)
        await cache.client.set(keys.dedupe, b"\xff")
        before = await asyncio.gather(*(cache.client.dump(key) for key in keys.watched))
        assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
            AdmissionState.UNAVAILABLE
        )
        assert not await store.cancel(envelope.job_id)
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            assert not await asyncio.to_thread(runtime.admission.cancel, envelope.job_id)
        assert await asyncio.gather(*(cache.client.dump(key) for key in keys.watched)) == before


@pytest.mark.parametrize("corrupt_key", ["totals", "subject", "ingress", "handoff", "pending", "payload", "dedupe"])
async def test_wrong_key_types_never_partially_release_admission(
    test_container: AsyncContainer, corrupt_key: str
) -> None:
    """EXEC cannot roll back WRONGTYPE errors, so both adapters reject them before queuing any writes."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("wrong-key-type", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        keys = admission_keys(namespace.value, envelope)
        key = getattr(keys, corrupt_key)
        await cache.client.delete(key)
        if corrupt_key in {"totals", "handoff", "pending"}:
            await cache.client.set(key, "wrong-type")
        else:
            await cache.client.hset(key, mapping={"unexpected": "wrong-type"})
        before = await asyncio.gather(*(cache.client.dump(key) for key in keys.watched))
        assert not await store.cancel(envelope.job_id)
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            assert await asyncio.to_thread(runtime.admission.finish, envelope.job_id, JobOutcome.CANCELLED) is None
        after = await asyncio.gather(*(cache.client.dump(key) for key in keys.watched))
        assert after == before
        ticket = await store.get(envelope.job_id)
        assert ticket is not None
        assert ticket.state == TicketState.ACCEPTED


async def test_missing_global_capacity_cannot_be_reset_by_new_admission(test_container: AsyncContainer) -> None:
    """An intact pending index prevents missing aggregate counters from becoming a new empty namespace."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        first = await payloads.seal("capacity-owner", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(first.reference, clock, job_id=first.job_id)
        assert (await store.admit(envelope, first, AdmissionLimits(2, 2, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        keys = admission_keys(namespace.value, envelope)
        await cache.client.delete(keys.totals)
        next_payload = await payloads.seal("capacity-reset", PRIVATE_BODY, clock.now() + 300)
        next_envelope = job_envelope(next_payload.reference, clock, job_id=next_payload.job_id, dedupe_key="b" * 64)
        assert (await store.admit(next_envelope, next_payload, AdmissionLimits(2, 2, safety), clock.now())).state == (
            AdmissionState.UNAVAILABLE
        )
        assert await store.get(next_envelope.job_id) is None
        assert (await payloads.load(first.reference, first.job_id)).state == PayloadState.READY
        assert (await payloads.load(next_payload.reference, next_payload.job_id)).state == PayloadState.MISSING
        assert not await cache.client.exists(keys.totals)


async def test_corrupt_ticket_pointer_cannot_delete_another_payload(test_container: AsyncContainer) -> None:
    """Persisted pointers must match the original envelope instead of trusting arbitrary namespaced keys."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("corrupt-pointer", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        keys = admission_keys(namespace.value, envelope)
        other = namespace.value + "payload:" + "f" * 32
        await cache.client.set(other, "synthetic-other-payload")
        raw = await cache.get(keys.ticket)
        raw["payload_key"] = other
        await cache.set(keys.ticket, raw)
        assert not await store.cancel(envelope.job_id)
        assert await store.get(envelope.job_id) is None
        assert await cache.client.get(other) == b"synthetic-other-payload"
        assert (await payloads.load(sealed.reference, sealed.job_id)).state == PayloadState.READY


async def test_native_publication_replay_preserves_schedule_and_stale_ack_fence(
    test_container: AsyncContainer,
) -> None:
    """Native and async publishers share first schedule, immutable envelope, and stage acknowledgement fences."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("native-publication", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        registered = await store.register_publication(envelope, clock.now() + 10)
        assert registered.state == EnqueueState.ENQUEUED
        assert registered.should_publish
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            repeated = await asyncio.to_thread(runtime.admission.register_publication, envelope, clock.now() + 20)
            assert repeated.state == EnqueueState.EXISTS
            assert repeated.should_publish
            assert repeated.not_before == registered.not_before
            with pytest.raises(ValueError, match="conflicts"):
                await asyncio.to_thread(
                    runtime.admission.register_publication, envelope.model_copy(update={"message_id": 2})
                )
            clock.value += 10
            owner = secrets.token_hex(16)
            assert (
                await asyncio.to_thread(
                    runtime.admission.claim_stage, envelope.job_id, JobStage.MEDIA, clock.now(), owner
                )
                is not None
            )
            ready = await asyncio.to_thread(runtime.admission.handoff_stage, envelope.job_id, owner, JobStage.BROWSER)
            assert ready is not None
            assert registered.ticket is not None
            assert not await store.ack_publication(envelope.job_id, registered.ticket.stage, registered.ticket.attempt)
            assert await asyncio.to_thread(
                runtime.admission.ack_publication, envelope.job_id, ready.stage, ready.attempt
            )
            assert not (await store.register_publication(envelope)).should_publish
        assert (await payloads.load(sealed.reference, sealed.job_id)).state == PayloadState.READY


async def test_admission_does_not_extend_an_existing_ciphertext_ttl(test_container: AsyncContainer) -> None:
    """Admission adopts a matching prepared ciphertext without extending its original retention."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("existing-ciphertext", PRIVATE_BODY, clock.now() + 300)
        assert await payloads.put(sealed) == PayloadState.READY
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        key = admission_keys(namespace.value, envelope).payload
        await cache.client.pexpire(key, 30_000)
        before = await cache.client.pttl(key)
        assert (await store.admit(envelope, sealed, AdmissionLimits(1, 1, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        assert 0 < await cache.client.pttl(key) <= before


async def test_missing_subject_counter_blocks_owner_completion(test_container: AsyncContainer) -> None:
    """A live runner cannot refund uncertain capacity even when its execution token is valid."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = replace(UsageSafetyCaps(), global_pending=2)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("missing-subject", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(sealed.reference, clock, job_id=sealed.job_id)
        assert (await store.admit(envelope, sealed, AdmissionLimits(2, 2, safety), clock.now())).state == (
            AdmissionState.ACCEPTED
        )
        owner = secrets.token_hex(16)
        assert await store.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), owner) is not None
        keys = admission_keys(namespace.value, envelope)
        await cache.client.delete(keys.subject)
        assert await store.finish(envelope.job_id, JobOutcome.COMPLETE, token=owner) is None
        raw = await cache.client.get(keys.ticket)
        assert json.loads(raw)["state"] == TicketState.RUNNING
        assert (await payloads.load(sealed.reference, sealed.job_id)).state == PayloadState.READY


@pytest.mark.parametrize("native_completions", [0, 1, 2])
async def test_concurrent_completion_releases_once_and_preserves_other_work(
    test_container: AsyncContainer, native_completions: int
) -> None:
    """Both runtimes replay one terminal write without refunding another ticket's capacity or ciphertext."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        completed = await payloads.seal("concurrent-completion", PRIVATE_BODY, clock.now() + 300)
        survivor = await payloads.seal("surviving-work", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(completed.reference, clock, job_id=completed.job_id)
        other = job_envelope(survivor.reference, clock, job_id=survivor.job_id, dedupe_key="b" * 64)
        limits = AdmissionLimits(2, 2, safety)
        assert (await store.admit(envelope, completed, limits, clock.now())).state == AdmissionState.ACCEPTED
        assert (await store.admit(other, survivor, limits, clock.now())).state == AdmissionState.ACCEPTED
        with WorkerRuntime(settings, clock, namespace, safety) as runtime:
            results = await asyncio.gather(
                *(store.finish(envelope.job_id, JobOutcome.COMPLETE) for _ in range(2 - native_completions)),
                *(
                    asyncio.to_thread(runtime.admission.finish, envelope.job_id, JobOutcome.COMPLETE)
                    for _ in range(native_completions)
                ),
            )
        assert all(result is not None and result.state == TicketState.TERMINAL for result in results)
        keys = admission_keys(namespace.value, envelope)
        assert await cache.client.execute_command("HGETALL", keys.totals) == {
            b"count": b"1",
            b"bytes": str(survivor.byte_size).encode(),
        }
        assert await cache.client.get(keys.subject) == b"1"
        assert await cache.client.get(keys.ingress) == b"1"
        assert await cache.client.zrange(keys.pending, 0, -1) == [other.job_id.encode()]
        assert await cache.client.zrange(keys.handoff, 0, -1) == [other.job_id.encode()]
        assert await cache.client.ttl(keys.ticket) > 0
        assert await cache.client.ttl(keys.dedupe) > 0
        assert (await payloads.load(completed.reference, completed.job_id)).state == PayloadState.MISSING
        assert (await payloads.load(survivor.reference, survivor.job_id)).state == PayloadState.READY
        remaining = await store.get(other.job_id)
        assert remaining is not None
        assert remaining.state == TicketState.ACCEPTED
