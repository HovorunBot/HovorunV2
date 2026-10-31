"""Real encrypted payloads, Celery broker serialization, and synchronous worker execution."""

import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
import redis.asyncio as redis
from dishka import AsyncContainer

from hovorunv2.application.clients.base import BaseMediaClient
from hovorunv2.application.clients.instagram import INSTAGRAM_CONFIG
from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.jobs.admission import JobStage
from hovorunv2.application.jobs.feature_job_runner import FeatureJobRunner
from hovorunv2.application.jobs.feature_jobs import (
    EnqueueState,
    JobEnvelope,
    JobOutcome,
    JobStatus,
    JobTarget,
    PayloadState,
)
from hovorunv2.application.jobs.http_ports import SyncHTTPPort
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.usage.policies import MEDIA_BUCKET, UsageSafetyCaps, UsageUnit, UsageWindow
from hovorunv2.application.usage.usage_ledger import AccountingSubject
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import (
    LazyBrowserService,
    NativeHTTPClient,
    WorkerQueuePublisher,
    WorkerRuntime,
    worker_child_context,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.feature_queue import CeleryFeatureQueue
from tests.utils.jobs import (
    PRIVATE_BODY,
    PRIVATE_URL,
    admit_test_job,
    broker_envelopes,
    job_envelope,
)
from tests.utils.runtime import ManualClock, runtime_cache


class VideoMediaClient(BaseMediaClient):
    """Synthetic client returning a video payload for provider commands."""

    def __init__(self) -> None:
        super().init_worker(INSTAGRAM_CONFIG)

    def extract_media_sync(self, url: str, http: SyncHTTPPort, extractor: NativeMediaExtractor) -> RichMediaPayload:
        """Return video payload to trigger video split variant."""
        return RichMediaPayload(
            author_name="test_author",
            author_handle="@test",
            author_url="https://synthetic.invalid/author",
            content="test caption",
            media_items=[MediaItem(url="https://synthetic.invalid/video.mp4", is_video=True)],
        )


class RecordingDelivery:
    """Synthetic synchronous delivery capturing calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def deliver(
        self,
        chat_id: int,
        reply_to_message_id: int,
        payload: RichMediaPayload,
        platform_label: str,
        sender_label: str | None = None,
    ) -> None:
        """Capture delivery payload without real Telegram network calls."""
        self.calls.append({"chat_id": chat_id, "payload": payload})


async def _seed_entities(test_container: AsyncContainer, user_id: int, group_id: int) -> tuple[int, int]:
    user_store = await test_container.get(UserStore)
    chat_store = await test_container.get(ChatStore)
    actor_ctx = ActorContext.cli(reason="synthetic-test")
    await user_store.ingest_user(user_id, actor=actor_ctx)
    chat = await chat_store.get_or_create_chat(group_id, actor=actor_ctx)
    await chat_store.update_status(group_id, ChatStatus.APPROVED, actor=actor_ctx)
    return chat.id, chat.generation


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


async def test_runner_never_executes_missing_or_expired_payload(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Missing/expired input becomes a safe terminal outcome without external execution."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    chat_id, generation = await _seed_entities(test_container, 390001, -390001)
    async with runtime_cache(test_container) as (cache, namespace):
        envelope = job_envelope(
            "b" * 32,
            clock,
            target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=-390001),
        )
        await admit_test_job(settings, cache, namespace, clock, envelope)
        await cache.delete(namespace.value + "payload:" + envelope.payload_ref)
        with worker_child_context(settings, namespace=namespace, clock=clock) as runner:
            assert runner.run(envelope.model_dump(mode="json")) == JobOutcome.PAYLOAD_UNAVAILABLE.value

        expired_envelope = job_envelope(
            "c" * 32,
            clock,
            job_id="expired-job",
            dedupe_key="c" * 64,
            target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=-390001),
        )
        await admit_test_job(settings, cache, namespace, clock, expired_envelope)
        clock.value = expired_envelope.deadline + 1
        with worker_child_context(settings, namespace=namespace, clock=clock) as runner:
            assert runner.run(expired_envelope.model_dump(mode="json")) == JobOutcome.EXPIRED.value


def _assert_envelope_invariants(envelope: JobEnvelope) -> None:
    for key in ("text", "url", "message", "raw_payload"):
        with pytest.raises(ValueError, match="Extra inputs"):
            JobEnvelope.model_validate({**envelope.model_dump(mode="json"), key: PRIVATE_URL})


async def test_real_celery_messages_are_private_and_duplicate_execution_is_blocked(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Concurrent publication may duplicate messages, but exactly one claim can invoke the real runner."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    chat_id, generation = await _seed_entities(test_container, 390001, -390001)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            sealed = await payloads.seal("feature-job", PRIVATE_BODY, clock.now() + 300)
            await payloads.put(sealed)
            envelope = job_envelope(
                sealed.reference,
                clock,
                target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=-390001),
            )
            await admit_test_job(settings, cache, namespace, clock, envelope)

            publications = await asyncio.gather(*(queue.enqueue(envelope) for _ in range(8)))
            assert publications.count(EnqueueState.ENQUEUED) == 1
            assert publications.count(EnqueueState.EXISTS) == 7

            messages = await asyncio.to_thread(broker_envelopes, settings, namespace.value)
            assert messages
            assert all(message == envelope.model_dump(mode="json") for message in messages)

            with worker_child_context(settings, namespace=namespace, clock=clock) as runner:
                outcomes = [runner.run(message) for message in messages]
                assert outcomes[0] == JobOutcome.FAILED.value
                assert all(outcome == JobOutcome.FAILED.value for outcome in outcomes)

            inspected = await queue.inspect(envelope.job_id)
            assert inspected is not None
            assert inspected.status == JobStatus.TERMINAL
            assert inspected.outcome == JobOutcome.FAILED
            _assert_envelope_invariants(envelope)
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


async def test_post_extraction_split_feature_recheck_blocks_delivery_and_charges_zero(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Disabled video variant blocks delivery, charges zero units/events, and settles budget."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    chat_id, generation = await _seed_entities(test_container, 390001, -390001)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        sealed = await payloads.seal("split-job", PRIVATE_BODY, clock.now() + 300)
        await payloads.put(sealed)
        envelope = job_envelope(
            sealed.reference,
            clock,
            job_id="split-job",
            feature="instagram_post",
            target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=-390001),
        )
        await admit_test_job(settings, cache, namespace, clock, envelope)

        delivery = RecordingDelivery()
        video_client = VideoMediaClient()
        with (
            WorkerDatabase(settings, FEATURE_REGISTRY, UsageSafetyCaps()) as database,
            WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime,
            NativeHTTPClient() as http,
        ):
            translation = NativeTranslationService(http, settings, database.policies)
            extractor = NativeMediaExtractor()
            browser = LazyBrowserService()
            publisher = WorkerQueuePublisher(settings.valkey_url, namespace, UsageSafetyCaps(), clock)
            runner = FeatureJobRunner(
                admission=runtime.admission,
                payloads=runtime.payloads,
                budgets=runtime.budgets,
                policies=database.policies,
                ledger=database.ledger,
                http=http,
                delivery=delivery,
                translation=translation,
                extractor=extractor,
                clock=clock,
                settings=settings,
                registry=FEATURE_REGISTRY,
                browser=browser,
                publisher=publisher,
                clients=[video_client],
            )
            outcome = runner.run(envelope.model_dump(mode="json"), JobStage.MEDIA)
            assert outcome == JobOutcome.COMPLETE.value
            assert len(delivery.calls) == 0

            ticket = runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.outcome == JobOutcome.COMPLETE

            subject = AccountingSubject(envelope.target.kind, envelope.target.telegram_id)
            total = database.ledger.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), int(clock.now()))
            assert total == 0


async def test_repeated_tasks_cannot_invoke_second_execution_and_stale_owner_fenced(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Repeated tasks cannot invoke a second execution (atomic claim rejection). Stale owner is fenced."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        sealed = await payloads.seal("repeat-job", PRIVATE_BODY, clock.now() + 300)
        await payloads.put(sealed)
        envelope = job_envelope(sealed.reference, clock, job_id="repeat-job")
        await admit_test_job(settings, cache, namespace, clock, envelope)

        with WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime:
            claim1 = runtime.admission.claim_stage(
                envelope.job_id, JobStage.MEDIA, clock.now(), "token-1", expected_envelope=envelope
            )
            assert claim1 is not None

            claim2 = runtime.admission.claim_stage(
                envelope.job_id, JobStage.MEDIA, clock.now(), "token-2", expected_envelope=envelope
            )
            assert claim2 is None

            stale_finish = runtime.admission.finish(
                envelope.job_id,
                JobOutcome.COMPLETE,
                token="wrong-stale-token",  # noqa: S106
            )
            assert stale_finish is None

            legit_finish = runtime.admission.finish(
                envelope.job_id,
                JobOutcome.COMPLETE,
                token="token-1",  # noqa: S106
            )
            assert legit_finish is not None
            assert legit_finish.outcome == JobOutcome.COMPLETE


@pytest.mark.subprocess
@pytest.mark.slow
async def test_native_prefork_worker_processes_job_end_to_end_under_werror(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Verify native prefork worker executes cleanly in child process under -W error."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    chat_id, generation = await _seed_entities(test_container, 390001, -390001)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        sealed = await payloads.seal("probe-job", PRIVATE_BODY, clock.now() + 300)
        await payloads.put(sealed)
        envelope = job_envelope(
            sealed.reference,
            clock,
            job_id="probe-job",
            target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=-390001),
        )
        await admit_test_job(settings, cache, namespace, clock, envelope)

        probe_path = Path(__file__).parents[2] / "utils" / "native_worker_probe.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-W",
            "error",
            str(probe_path),
            postgres_database_url,
            settings.valkey_url,
            namespace.value,
            json.dumps(envelope.model_dump(mode="json")),
            settings.cache_encryption_key or "",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise
        assert process.returncode == 0, stderr.decode()
        assert not stderr, stderr.decode()
        result = json.loads(stdout)
        assert result["status"] == "ok"
