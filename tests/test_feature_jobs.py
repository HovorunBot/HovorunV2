"""Encrypted payloads and real Celery feature messages, without production workers."""

import asyncio
import json
import time
from dataclasses import replace
from http import HTTPStatus
from queue import Empty

import aiohttp
import pytest
import redis.asyncio as redis
from aioresponses import aioresponses
from celery import Celery
from dishka import AsyncContainer

from hovorunv2.application.admission import AdmissionLimits
from hovorunv2.application.budgets import RuntimeNamespace
from hovorunv2.application.feature_jobs import EnqueueState, JobEnvelope, JobOutcome, JobStatus, JobTarget, PayloadState
from hovorunv2.application.features import TriggerKind
from hovorunv2.application.services.feature_job_runner import FeatureJobRunner
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.feature_queue import CeleryFeatureQueue, FeatureWorkerBridge
from tests.test_usage_policies import FEATURE
from tests.utils.runtime import ManualClock, runtime_cache

PRIVATE_URL = "https://synthetic.invalid/private-request?secret=never-store"
PRIVATE_BODY = (PRIVATE_URL + "\nprivate caption").encode()


def job_envelope(reference: str, clock: ManualClock, *, job_id: str = "feature-job", **changes: object) -> JobEnvelope:
    """Construct minimal synthetic operational metadata without payload content."""
    values: dict[str, object] = {
        "job_id": job_id,
        "payload_ref": reference,
        "feature": FEATURE,
        "target": JobTarget(kind=TargetKind.GROUP, entity_id=1, generation=1, telegram_id=-390001),
        "actor_id": 390001,
        "actor_generation": 1,
        "trigger": TriggerKind.DIRECT,
        "charged_kind": TargetKind.USER,
        "charged_telegram_id": 390001,
        "accepted_at": clock.now(),
        "deadline": clock.now() + 300,
        "dedupe_key": "a" * 64,
        "feature_epoch": 7,
        "message_id": 1,
    }
    values.update(changes)
    return JobEnvelope.model_validate(values)


async def admit_test_job(
    settings: Settings, cache: CacheService, namespace: RuntimeNamespace, clock: ManualClock, envelope: JobEnvelope
) -> None:
    """Accept real counted ciphertext before exercising the publication boundary."""
    payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
    raw = await cache.get(namespace.value + "payload:" + envelope.payload_ref)
    if raw is None:
        sealed = await payloads.seal(envelope.job_id, PRIVATE_BODY, envelope.deadline)
        sealed = replace(sealed, reference=envelope.payload_ref)
    else:
        blob = json.dumps(raw, separators=(",", ":"))
        from_payload = await payloads.seal(envelope.job_id, PRIVATE_BODY, envelope.deadline)
        sealed = replace(from_payload, reference=envelope.payload_ref, blob=blob, byte_size=len(blob.encode()))
    result = await ValkeyAdmissionStore(cache, namespace).admit(
        envelope, sealed, AdmissionLimits(10, 10, UsageSafetyCaps()), clock.now()
    )
    assert result.ticket is not None


class HTTPExecutor:
    """Exercise real external HTTP exception handling through the executor boundary."""

    async def execute(self, envelope: JobEnvelope, payload: bytes) -> JobOutcome:
        """Use actual HTTP I/O intercepted only at the external API boundary."""
        url = payload.decode().splitlines()[0]
        async with aiohttp.ClientSession() as session, session.get(url, raise_for_status=True) as response:
            await response.read()
        return JobOutcome.COMPLETE


async def test_payload_encryption_size_deadline_and_safe_missing_states(test_container: AsyncContainer) -> None:
    """Only ciphertext reaches Valkey; retention covers original deadline plus cleanup without renewal."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        sealed = await store.seal("feature-job", PRIVATE_BODY, clock.now() + 300)
        assert PRIVATE_URL not in sealed.blob
        assert sealed.byte_size == len(sealed.blob.encode())
        assert await store.put(sealed) == PayloadState.READY
        client = redis.from_url(settings.valkey_url)
        try:
            before = await client.pttl(namespace.value + "payload:" + sealed.reference)
            clock.value += 10
            assert await store.put(sealed) == PayloadState.READY
            assert await client.pttl(namespace.value + "payload:" + sealed.reference) <= before
            assert 350_000 <= before <= 360_000
            raw = await client.get(namespace.value + "payload:" + sealed.reference)
            assert PRIVATE_BODY not in raw
        finally:
            await client.aclose()
        loaded = await store.load(sealed.reference, "feature-job")
        assert loaded.data == PRIVATE_BODY
        assert (await store.load(sealed.reference, "other-job")).state == PayloadState.UNREADABLE
        raw = json.loads(sealed.blob)
        raw["ciphertext"] = "invalid-synthetic-ciphertext"
        await cache.set(namespace.value + "payload:" + sealed.reference, raw, expire=300)
        assert (await store.load(sealed.reference, "feature-job")).state == PayloadState.UNREADABLE
        await store.delete(sealed.reference)
        assert (await store.load(sealed.reference, "feature-job")).state == PayloadState.MISSING
        with pytest.raises(ValueError, match="bound"):
            await store.seal("oversized", b"x" * (64 * 1024), clock.now() + 300)


async def test_runner_never_executes_missing_or_expired_payload(test_container: AsyncContainer) -> None:
    """Missing/expired input becomes a safe terminal outcome without external execution."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        runner = FeatureJobRunner(HTTPExecutor(), payloads, clock)
        envelope = job_envelope("b" * 32, clock)
        with aioresponses() as http:
            assert await runner.run(envelope.model_dump(mode="json")) == JobOutcome.PAYLOAD_UNAVAILABLE
            clock.value = envelope.deadline
            assert await runner.run(envelope.model_dump(mode="json")) == JobOutcome.EXPIRED
            assert not http.requests


def broker_envelopes(settings: Settings, namespace: str) -> list[dict[str, object]]:
    """Consume real broker messages without starting a production executor or worker."""
    app = Celery("test_features", broker=settings.valkey_url)
    app.conf.broker_transport_options = {"global_keyprefix": namespace}
    envelopes = []
    try:
        with app.connection_for_read() as connection, connection.SimpleQueue(namespace + "features") as queue:
            while True:
                try:
                    message = queue.get_nowait()
                except Empty:
                    return envelopes
                assert PRIVATE_URL not in json.dumps(message.payload)
                assert message.headers["kwargsrepr"] == "<feature envelope>"
                envelopes.append(message.payload[1]["envelope"])
                message.ack()
    finally:
        app.close()


async def test_real_celery_messages_are_private_and_duplicate_execution_is_blocked(
    test_container: AsyncContainer,
) -> None:
    """Concurrent publication may duplicate messages, but exactly one claim can invoke the real runner."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            sealed = await payloads.seal("feature-job", PRIVATE_BODY, clock.now() + 300)
            await payloads.put(sealed)
            envelope = job_envelope(sealed.reference, clock)
            await admit_test_job(settings, cache, namespace, clock, envelope)
            publications = await asyncio.gather(*(queue.enqueue(envelope) for _ in range(8)))
            assert publications.count(EnqueueState.ENQUEUED) == 1
            assert publications.count(EnqueueState.EXISTS) == 7
            assert await queue.enqueue(envelope) == EnqueueState.EXISTS
            messages = await asyncio.to_thread(broker_envelopes, settings, namespace.value)
            assert messages
            assert all(message == envelope.model_dump(mode="json") for message in messages)
            bridge = FeatureWorkerBridge(FeatureJobRunner(HTTPExecutor(), payloads, clock), queue)
            with aioresponses() as http:
                http.get(PRIVATE_URL, status=HTTPStatus.FORBIDDEN)
                await asyncio.gather(*(bridge.run_feature_job(message) for message in messages))
                assert sum(len(requests) for requests in http.requests.values()) == 1
            inspected = await queue.inspect(envelope.job_id)
            assert inspected is not None
            assert inspected.status == JobStatus.TERMINAL
            assert inspected.outcome == JobOutcome.FAILED
            assert await bridge.run_feature_job(envelope.model_dump(mode="json")) == JobOutcome.FAILED
            for key in ("text", "url", "message", "raw_payload"):
                with pytest.raises(ValueError, match="Extra inputs"):
                    JobEnvelope.model_validate({**envelope.model_dump(mode="json"), key: PRIVATE_URL})
            with pytest.raises(ValueError, match="conflicts"):
                await queue.enqueue(envelope.model_copy(update={"feature_epoch": 8}))
            clock.value = envelope.deadline
            assert await queue.enqueue(envelope) == EnqueueState.EXPIRED
        finally:
            await queue.close()
    async with redis.from_url(settings.valkey_url) as client:
        assert not [key async for key in client.scan_iter(match="*" + namespace.value + "*")]


async def test_missing_encryption_and_actual_ciphertext_bound_fail_before_write(test_container: AsyncContainer) -> None:
    """No plaintext fallback exists, and ciphertext overhead counts against admission's per-request byte cap."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        missing = settings.model_copy(update={"cache_encryption_key": None})
        store = ValkeyPayloadStore(cache, missing, clock, namespace, UsageSafetyCaps())
        with pytest.raises(ValueError, match="configured encryption"):
            await store.seal("no-secret", PRIVATE_BODY, clock.now() + 300)
        bounded = ValkeyPayloadStore(cache, settings, clock, namespace, replace(UsageSafetyCaps(), payload_bytes=200))
        with pytest.raises(ValueError, match="storage bound"):
            await bounded.seal("overhead", b"x" * 150, clock.now() + 300)
