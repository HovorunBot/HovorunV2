"""Real Valkey ownership and cancellation contracts around Celery's at-least-once publication."""

import asyncio
import json
import time

import pytest
import redis.asyncio as redis
from dishka import AsyncContainer

from hovorunv2.application.feature_jobs import EnqueueState, JobOutcome, JobStatus
from hovorunv2.application.services.feature_job_runner import FeatureJobRunner
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.feature_queue import CeleryFeatureQueue, FeatureWorkerBridge
from tests.test_feature_jobs import PRIVATE_BODY, HTTPExecutor, admit_test_job, broker_envelopes, job_envelope
from tests.utils.runtime import ManualClock, runtime_cache


async def test_republication_after_lost_ack_preserves_envelope_and_exclusive_claim(
    test_container: AsyncContainer,
) -> None:
    """A real published message with a lost application acknowledgement can be resent but executed only once."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            envelope = job_envelope("b" * 32, clock)
            await admit_test_job(settings, cache, namespace, clock, envelope)
            assert await queue.enqueue(envelope) == EnqueueState.ENQUEUED
            async with redis.from_url(settings.valkey_url) as client:
                # Real state at the crash window: broker accepted, application publish acknowledgement absent.
                key = namespace.value + "admission:ticket:" + envelope.job_id
                raw = json.loads(await client.get(key))
                raw["published"] = 0
                await client.set(key, json.dumps(raw))
            assert await queue.enqueue(envelope) == EnqueueState.EXISTS
            messages = await asyncio.to_thread(broker_envelopes, settings, namespace.value)
            assert len(messages) == 2
            assert messages[0] == messages[1] == envelope.model_dump(mode="json")
            claims = await asyncio.gather(*(queue.claim(envelope) for _ in range(12)))
            owners = [claim for claim in claims if claim is not None]
            assert len(owners) == 1
            assert (await queue.inspect(envelope.job_id)).status == JobStatus.RUNNING
            assert not await queue.finish(envelope.job_id, "wrong-owner", JobOutcome.COMPLETE)
            assert await queue.finish(envelope.job_id, owners[0], JobOutcome.COMPLETE)
            assert await queue.finish(envelope.job_id, owners[0], JobOutcome.COMPLETE)
            assert not await queue.finish(envelope.job_id, owners[0], JobOutcome.FAILED)
            assert await queue.claim(envelope) is None
            assert (await queue.inspect(envelope.job_id)).outcome == JobOutcome.COMPLETE
        finally:
            await queue.close()


async def test_cancellation_tombstones_pending_but_does_not_complete_active_work(
    test_container: AsyncContainer,
) -> None:
    """Cancel-before-claim blocks all execution; active requests retain ownership and await the actual outcome."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            pending = job_envelope("a" * 32, clock, job_id="cancel-pending")
            await admit_test_job(settings, cache, namespace, clock, pending)
            assert await queue.enqueue(pending) == EnqueueState.ENQUEUED
            assert await queue.cancel(pending.job_id)
            assert await queue.claim(pending) is None
            inspected = await queue.inspect(pending.job_id)
            assert inspected.status == JobStatus.TERMINAL
            assert inspected.outcome == JobOutcome.CANCELLED
            assert not await queue.cancel(pending.job_id)
            active = job_envelope("b" * 32, clock, job_id="cancel-active", dedupe_key="b" * 64)
            await admit_test_job(settings, cache, namespace, clock, active)
            assert await queue.enqueue(active) == EnqueueState.ENQUEUED
            owner = await queue.claim(active)
            assert owner is not None
            assert await queue.cancel(active.job_id)
            inspected = await queue.inspect(active.job_id)
            assert inspected.status == JobStatus.CANCELLING
            assert inspected.outcome is None
            assert await queue.claim(active) is None
            assert await queue.finish(active.job_id, owner, JobOutcome.COMPLETE)
            assert (await queue.inspect(active.job_id)).outcome == JobOutcome.COMPLETE
            assert not await queue.cancel("unknown-job")
        finally:
            await queue.close()


async def test_original_schedule_deadline_and_missing_claim_fail_closed(test_container: AsyncContainer) -> None:
    """Controlled time never renews scheduling or reclaims a possibly live owner after its deadline."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            envelope = job_envelope("a" * 32, clock)
            assert await queue.claim(envelope) is None
            await admit_test_job(settings, cache, namespace, clock, envelope)
            assert await queue.enqueue(envelope, not_before=clock.now() + 10) == EnqueueState.ENQUEUED
            assert await queue.enqueue(envelope, not_before=clock.now() + 20) == EnqueueState.EXISTS
            assert await queue.claim(envelope) is None
            clock.value += 10
            owner = await queue.claim(envelope)
            assert owner is not None
            clock.value = envelope.deadline
            assert await queue.claim(envelope) is None
            assert (await queue.inspect(envelope.job_id)).status == JobStatus.RUNNING
            assert await queue.finish(envelope.job_id, owner, JobOutcome.EXPIRED)
            fresh = job_envelope("b" * 32, clock, job_id="expires-before-start", dedupe_key="b" * 64)
            await admit_test_job(settings, cache, namespace, clock, fresh)
            assert await queue.enqueue(fresh) == EnqueueState.ENQUEUED
            clock.value = fresh.deadline
            assert await queue.claim(fresh) is None
            inspected = await queue.inspect(fresh.job_id)
            assert inspected.status == JobStatus.TERMINAL
            assert inspected.outcome == JobOutcome.EXPIRED
            with pytest.raises(ValueError, match="finite"):
                await queue.enqueue(fresh, not_before=float("nan"))
            with pytest.raises(ValueError, match="identity"):
                await queue.inspect("../other-key")
        finally:
            await queue.close()


async def test_runner_bridge_claims_before_private_payload_load(test_container: AsyncContainer) -> None:
    """A cancelled broker message cannot decrypt or execute; missing payload yields a safe terminal receipt."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        try:
            sealed = await payloads.seal("cancelled", PRIVATE_BODY, clock.now() + 300)
            await payloads.put(sealed)
            envelope = job_envelope(sealed.reference, clock, job_id="cancelled")
            await admit_test_job(settings, cache, namespace, clock, envelope)
            await queue.enqueue(envelope)
            await queue.cancel(envelope.job_id)
            bridge = FeatureWorkerBridge(FeatureJobRunner(HTTPExecutor(), payloads, clock), queue)
            assert await bridge.run_feature_job(envelope.model_dump(mode="json")) == JobOutcome.CANCELLED
            missing = job_envelope("a" * 32, clock, job_id="missing", dedupe_key="b" * 64)
            await admit_test_job(settings, cache, namespace, clock, missing)
            await queue.enqueue(missing)
            await payloads.delete(missing.payload_ref)
            assert await bridge.run_feature_job(missing.model_dump(mode="json")) == JobOutcome.PAYLOAD_UNAVAILABLE
            inspected = await queue.inspect(missing.job_id)
            assert inspected.status == JobStatus.TERMINAL
            assert inspected.outcome == JobOutcome.PAYLOAD_UNAVAILABLE
        finally:
            await queue.close()
