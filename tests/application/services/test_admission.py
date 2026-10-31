"""Bounded real-Valkey admission and crash-recoverable handoff to real Celery."""

import asyncio
import time
from dataclasses import replace

import pytest
import redis.asyncio as redis
from dishka import AsyncContainer

from hovorunv2.application.features.feature_access_service import FeatureAccessService
from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.jobs.admission import AdmissionLimits, AdmissionState, TicketState, incoming_action_key
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_jobs import JobOutcome, JobStatus, JobTarget, PayloadState
from hovorunv2.application.ports import ChatStore, CommandStore, UserStore
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.queues.feature_queue import CeleryFeatureQueue
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
from tests.utils.identities import FEATURE
from tests.utils.jobs import PRIVATE_BODY, job_envelope
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.usage import USER, usage_subjects


async def test_parallel_admission_bounds_subject_ingress_and_duplicate_capacity(test_container: AsyncContainer) -> None:
    """Concurrent duplicate and new arrivals never exceed subject capacity or consume extra duplicate slots."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    safety = replace(UsageSafetyCaps(), global_pending=20)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        store = ValkeyAdmissionStore(cache, namespace)
        limits = AdmissionLimits(2, 3, safety)
        prepared = []
        for index in range(12):
            job_id = f"arrival-{index}"
            sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
            envelope = job_envelope(
                sealed.reference,
                clock,
                job_id=job_id,
                dedupe_key=incoming_action_key(FEATURE, -390001, 390001, index + 1, TriggerKind.DIRECT),
                message_id=index + 1,
            )
            prepared.append((envelope, sealed))
        results = await asyncio.gather(
            *[store.admit(envelope, sealed, limits, clock.now()) for envelope, sealed in prepared]
        )
        assert sum(result.state == AdmissionState.ACCEPTED for result in results) == 2
        assert sum(result.state == AdmissionState.FULL for result in results) == 10
        winner = next(index for index, result in enumerate(results) if result.state == AdmissionState.ACCEPTED)
        duplicate = await store.admit(*prepared[winner], limits, clock.now())
        assert duplicate.state == AdmissionState.DUPLICATE
        accepted = await store.recoverable(clock.now())
        assert len(accepted) == 2
        assert len(await store.recoverable(clock.now(), 1)) == 1
        first = accepted[0]
        assert (await store.finish(first.envelope.job_id, JobOutcome.COMPLETE)).state == TicketState.TERMINAL
        assert (await store.finish(first.envelope.job_id, JobOutcome.COMPLETE)).state == TicketState.TERMINAL
        with pytest.raises(ValueError, match="conflicts"):
            await store.finish(first.envelope.job_id, JobOutcome.FAILED)
        loser = next(index for index, result in enumerate(results) if result.state == AdmissionState.FULL)
        assert (await store.admit(*prepared[loser], limits, clock.now())).state == AdmissionState.ACCEPTED
        assert len(await store.recoverable(clock.now())) == 2
        assert (await payloads.load(first.envelope.payload_ref, first.envelope.job_id)).state == PayloadState.MISSING


@pytest.mark.parametrize("bound", ["count", "bytes"])
async def test_parallel_global_ciphertext_bytes_and_overflow_preserve_accepted_jobs(
    test_container: AsyncContainer,
    bound: str,
) -> None:
    """Different subjects share exact stored-byte and global-count limits; overflow never evicts older work."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        store = ValkeyAdmissionStore(cache, namespace)
        entries = []
        for index in range(10):
            job_id = f"global-{index}"
            sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
            actor = 390001 + index
            envelope = job_envelope(
                sealed.reference,
                clock,
                job_id=job_id,
                actor_id=actor,
                charged_telegram_id=actor,
                dedupe_key=f"{index:064x}",
            )
            entries.append((envelope, sealed))
        size = entries[0][1].byte_size
        assert all(sealed.byte_size == size for _envelope, sealed in entries)
        safety = replace(
            UsageSafetyCaps(),
            payload_bytes=size,
            total_payload_bytes=(2 if bound == "bytes" else 8) * size,
            global_pending=2 if bound == "count" else 8,
        )
        limits = AdmissionLimits(10, 10, safety)
        results = await asyncio.gather(
            *[store.admit(envelope, sealed, limits, clock.now()) for envelope, sealed in entries]
        )
        assert sum(result.state == AdmissionState.ACCEPTED for result in results) == 2
        assert len(await store.recoverable(clock.now())) == 2
        client = redis.from_url(settings.valkey_url)
        try:
            totals = await client.execute_command("HGETALL", namespace.value + "admission:totals")
            assert int(totals[b"count"]) == 2
            assert int(totals[b"bytes"]) == 2 * size
            for (envelope, _sealed), result in zip(entries, results, strict=True):
                state = (await payloads.load(envelope.payload_ref, envelope.job_id)).state
                assert state == (
                    PayloadState.READY if result.state == AdmissionState.ACCEPTED else PayloadState.MISSING
                )
        finally:
            await client.aclose()


async def test_automatic_group_ingress_is_per_sender_and_shared_across_features(test_container: AsyncContainer) -> None:
    """One sender cannot occupy the default group queue; another sender can use remaining capacity."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        store = ValkeyAdmissionStore(cache, namespace)
        limits = AdmissionLimits(3, 1, replace(UsageSafetyCaps(), global_pending=3))
        for index, actor in enumerate((390001, 390001, 390002, 390003)):
            job_id = f"sender-{index}"
            sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
            envelope = job_envelope(
                sealed.reference,
                clock,
                job_id=job_id,
                actor_id=actor,
                trigger=TriggerKind.AUTOMATIC,
                charged_kind=TargetKind.GROUP,
                charged_telegram_id=-390001,
                dedupe_key=f"{index:064x}",
            )
            result = await store.admit(envelope, sealed, limits, clock.now())
            assert result.state == (AdmissionState.FULL if index == 1 else AdmissionState.ACCEPTED)
        assert len(await store.recoverable(clock.now())) == 3


async def admission_service(
    container: AsyncContainer, store: ValkeyAdmissionStore, queue: CeleryFeatureQueue, clock: ManualClock
) -> AdmissionService:
    """Compose the real application service and its real stores/adapters."""
    return AdmissionService(
        store,
        queue,
        await container.get(UsagePolicyService),
        await container.get(FeatureAccessService),
        await container.get(UserStore),
        await container.get(ChatStore),
        clock,
    )


async def test_handoff_recovers_crashes_before_enqueue_and_before_acknowledgement(
    test_container: AsyncContainer,
) -> None:
    """Accepted tickets retain capacity until real Celery enqueue succeeds and its acknowledgement is replayed."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        store = ValkeyAdmissionStore(cache, namespace)
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        unavailable = CeleryFeatureQueue(
            settings.model_copy(update={"valkey_url": "redis://127.0.0.1:1/0"}), clock, namespace, UsageSafetyCaps()
        )
        try:
            sealed = await payloads.seal("crash-handoff", PRIVATE_BODY, clock.now() + 300)
            envelope = job_envelope(sealed.reference, clock, job_id="crash-handoff")
            assert (
                await store.admit(envelope, sealed, AdmissionLimits(10, 10, UsageSafetyCaps()), clock.now())
            ).state == AdmissionState.ACCEPTED
            assert (await store.get(envelope.job_id)).envelope == envelope
            paused = await admission_service(test_container, store, unavailable, clock)
            assert (await paused.handoff(envelope.job_id)).state == AdmissionState.UNAVAILABLE
            assert len(await store.recoverable(clock.now())) == 1
            await queue.enqueue(envelope)
            # Simulate the process stopping after enqueue but before ticket acknowledgement.
            recovered = await admission_service(test_container, store, queue, clock)
            result = await recovered.recover()
            assert len(result) == 1
            assert result[0].state == AdmissionState.ENQUEUED
            assert not await store.recoverable(clock.now())
            assert (await recovered.handoff(envelope.job_id)).state == AdmissionState.ENQUEUED
            assert (await queue.inspect(envelope.job_id)).envelope == envelope
            assert await queue.cancel(envelope.job_id)
            assert (await queue.inspect(envelope.job_id)).status == JobStatus.TERMINAL
            await store.finish(envelope.job_id, JobOutcome.CANCELLED)
            assert (await recovered.handoff(envelope.job_id)).state == AdmissionState.TERMINAL
        finally:
            await unavailable.close()
            await queue.close()


async def test_application_admission_rechecks_actor_target_access_and_original_deadline(
    test_container: AsyncContainer,
) -> None:
    """Real current identity, trust and selected feature gate admission; denials reserve no resources."""
    user, _group = await usage_subjects(test_container)
    users = await test_container.get(UserStore)
    await users.set_trust(USER, is_trusted=True, actor=ActorContext.cli())
    commands = await test_container.get(CommandStore)
    await commands.enable_command(USER, FEATURE, actor=ActorContext.cli())
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        store = ValkeyAdmissionStore(cache, namespace)
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            service = await admission_service(test_container, store, queue, clock)
            sealed = await payloads.seal("private-action", PRIVATE_BODY, clock.now() + 300)
            envelope = job_envelope(
                sealed.reference,
                clock,
                job_id="private-action",
                actor_id=USER,
                target=JobTarget(
                    kind=TargetKind.USER, entity_id=user.entity_id, generation=user.generation, telegram_id=USER
                ),
                charged_telegram_id=USER,
                dedupe_key=incoming_action_key(FEATURE, USER, USER, 1, TriggerKind.DIRECT),
            )
            assert (
                await service.admit(envelope.model_copy(update={"actor_generation": 2}), sealed)
            ).state == AdmissionState.DENIED
            assert (
                await service.admit(envelope.model_copy(update={"dedupe_key": "b" * 64}), sealed)
            ).state == AdmissionState.DENIED
            for target in (
                envelope.target.model_copy(update={"entity_id": user.entity_id + 1}),
                envelope.target.model_copy(update={"generation": user.generation + 1}),
            ):
                assert (
                    await service.admit(envelope.model_copy(update={"target": target}), sealed)
                ).state == AdmissionState.DENIED
            for charge in (
                {"charged_kind": TargetKind.GROUP},
                {"charged_telegram_id": USER + 1},
            ):
                assert (await service.admit(envelope.model_copy(update=charge), sealed)).state == AdmissionState.DENIED
            assert await store.get(envelope.job_id) is None
            assert not await store.recoverable(clock.now())
            assert (await service.admit(envelope, sealed)).state == AdmissionState.ACCEPTED
            assert (await service.admit(envelope, sealed)).state == AdmissionState.DUPLICATE
            await users.set_trust(USER, is_trusted=False, actor=ActorContext.cli())
            assert (await service.admit(envelope, sealed)).state == AdmissionState.DENIED
            clock.value = envelope.deadline
            assert (await service.admit(envelope, sealed)).state == AdmissionState.EXPIRED
            assert (await service.handoff(envelope.job_id)).state == AdmissionState.EXPIRED
            assert (await store.get(envelope.job_id)).state == TicketState.ACCEPTED
            assert not await store.recoverable(clock.now())
            # Terminal cleanup must first verify execution is stopped; expiry alone does not free capacity.
            await store.finish(envelope.job_id, JobOutcome.EXPIRED)
            assert (await store.get(envelope.job_id)).state == TicketState.TERMINAL
        finally:
            await queue.close()


async def test_concurrent_duplicate_actions_keep_original_envelope_and_one_payload(
    test_container: AsyncContainer,
) -> None:
    """Parallel retries with fresh job IDs still share one stable incoming action and capacity slot."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        store = ValkeyAdmissionStore(cache, namespace)
        entries = []
        for index in range(8):
            job_id = f"duplicate-{index}"
            sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
            entries.append((job_envelope(sealed.reference, clock, job_id=job_id), sealed))
        limits = AdmissionLimits(10, 10, UsageSafetyCaps())
        results = await asyncio.gather(
            *[store.admit(envelope, sealed, limits, clock.now()) for envelope, sealed in entries]
        )
        assert sum(result.state == AdmissionState.ACCEPTED for result in results) == 1
        assert sum(result.state == AdmissionState.DUPLICATE for result in results) == 7
        original = next(result.ticket for result in results if result.state == AdmissionState.ACCEPTED)
        assert all(result.ticket.envelope == original.envelope for result in results)
        assert original.envelope.deadline == entries[0][0].deadline
        assert len(await store.recoverable(clock.now())) == 1
        states = [(await payloads.load(envelope.payload_ref, envelope.job_id)).state for envelope, _sealed in entries]
        assert states.count(PayloadState.READY) == 1
        assert states.count(PayloadState.MISSING) == 7
