"""Real encrypted payloads, Celery broker serialization, and synchronous worker execution."""

import asyncio
import json
import secrets
import sys
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
import redis.asyncio as redis
from billiard.exceptions import SoftTimeLimitExceeded
from dishka import AsyncContainer

from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionLimits, AdmissionState, JobStage, TicketState
from hovorunv2.application.jobs.feature_job_runner import (
    APPLICATION_FAILURE_NOTICE_TEXT,
    EXTRACTION_FAILURE_NOTICE_TEXT,
    LIMIT_FAILURE_NOTICE_TEXT,
    LIMIT_WAIT_NOTICE_TEXT,
    TIMEOUT_NOTICE_TEXT,
    FeatureJobRunner,
)
from hovorunv2.application.jobs.feature_jobs import (
    EnqueueState,
    JobEnvelope,
    JobOutcome,
    JobStatus,
    JobTarget,
    PayloadState,
)
from hovorunv2.application.jobs.http_ports import SyncBrowserPort, SyncHTTPPort
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import BrowserContinuationRequired, MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.instagram.embed import DisabledEmbedAdapter
from hovorunv2.application.media.payloads import (
    MediaItem,
    RichMediaPayload,
    pack_media_payload,
    unpack_media_payload,
)
from hovorunv2.application.media.providers.base import BaseMediaClient
from hovorunv2.application.media.providers.instagram import INSTAGRAM_CONFIG
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.usage.budgets import BudgetRequest, ReservationDecision, ReservationState, UnitAmount
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
from hovorunv2.infrastructure.queues.feature_queue import CeleryFeatureQueue
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
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
        super().__init__(INSTAGRAM_CONFIG)

    def extract_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        extractor: NativeMediaExtractor,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> RichMediaPayload:
        """Return video payload to trigger video split variant."""
        return RichMediaPayload(
            author_name="test_author",
            author_handle="@test",
            author_url="https://synthetic.invalid/author",
            content="test caption",
            media_items=[MediaItem(url="https://synthetic.invalid/video.mp4", is_video=True)],
        )


class TimedOutMediaClient(VideoMediaClient):
    """Raise Celery's cooperative timeout at the extraction boundary."""

    def extract_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        extractor: NativeMediaExtractor,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> RichMediaPayload:
        """Interrupt extraction before delivery or accounting can begin."""
        raise SoftTimeLimitExceeded


class FailedMediaClient(VideoMediaClient):
    """Exhaust extraction without usable post metadata."""

    def extract_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        extractor: NativeMediaExtractor,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> RichMediaPayload:
        """Fail before delivery so the worker must send terminal feedback."""
        msg = "Synthetic extraction failure."
        raise MediaExtractionError(msg)


class BrowserRequiredMediaClient(VideoMediaClient):
    """Require browser continuation after non-browser extraction is exhausted."""

    def extract_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        extractor: NativeMediaExtractor,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> RichMediaPayload:
        """Request continuation without launching or importing a browser."""
        raise BrowserContinuationRequired

    def extract_browser_media_sync(
        self, url: str, http: SyncHTTPPort, browser: SyncBrowserPort, *, deadline: ExtractionDeadline | None = None
    ) -> RichMediaPayload:
        """Return controlled media after the browser stage is explicitly enabled."""
        return super().extract_media_sync(url, http, NativeMediaExtractor())


class MetadataVideoClient(VideoMediaClient):
    """Return video metadata without downloadable media."""

    def extract_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        extractor: NativeMediaExtractor,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> RichMediaPayload:
        """Preserve video classification for post-extraction permission checks."""
        payload = super().extract_media_sync(url, http, extractor)
        payload.media_items.clear()
        payload.video_unavailable = True
        return payload


class BrokenMediaClient(VideoMediaClient):
    """Raise an application defect without exposing exception details to users."""

    def extract_media_sync(
        self,
        url: str,
        http: SyncHTTPPort,
        extractor: NativeMediaExtractor,
        *,
        deadline: ExtractionDeadline | None = None,
    ) -> RichMediaPayload:
        """Interrupt application execution with synthetic private diagnostic text."""
        raise ValueError(PRIVATE_URL)


class RecordingTelegram:
    """Synthetic synchronous Telegram text sender capturing calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> int:
        """Capture one feedback message."""
        self.calls.append((chat_id, text))
        return 1


class AllowFeedback:
    """Allow feedback for synthetic active targets."""

    def feedback_allowed(self, target: object) -> bool:
        """Return True for the controlled test target."""
        return True


class AllowGate:
    """Allow one delayed notice for synthetic targets."""

    def claim_delayed(self, target_key: str, ttl: int) -> bool:
        """Return True for the controlled test target."""
        return True


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


@pytest.mark.parametrize(
    ("client_type", "expected_outcome", "expected_notice", "browser_enabled"),
    [
        (VideoMediaClient, JobOutcome.COMPLETE, None, False),
        (MetadataVideoClient, JobOutcome.COMPLETE, None, False),
        (TimedOutMediaClient, JobOutcome.EXPIRED, TIMEOUT_NOTICE_TEXT, False),
        (FailedMediaClient, JobOutcome.FAILED, EXTRACTION_FAILURE_NOTICE_TEXT, False),
        (BrowserRequiredMediaClient, JobOutcome.FAILED, EXTRACTION_FAILURE_NOTICE_TEXT, False),
        (BrowserRequiredMediaClient, JobOutcome.COMPLETE, None, True),
        (BrokenMediaClient, JobOutcome.FAILED, APPLICATION_FAILURE_NOTICE_TEXT, False),
    ],
)
async def test_blocked_video_or_extraction_timeout_prevents_delivery_and_charges_zero(
    test_container: AsyncContainer,
    postgres_database_url: str,
    client_type: type[VideoMediaClient],
    expected_outcome: JobOutcome,
    expected_notice: str | None,
    *,
    browser_enabled: bool,
) -> None:
    """Disabled capture and soft timeouts both settle without delivery or consumption."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url, "browser_enabled": browser_enabled})
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
        telegram = RecordingTelegram()
        video_client = client_type()
        allowed_test_hosts = frozenset({"127.0.0.1", "localhost", "synthetic.invalid", "media.example.invalid"})
        with (
            WorkerDatabase(settings, FEATURE_REGISTRY, UsageSafetyCaps()) as database,
            WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime,
            NativeHTTPClient(allowed_hosts=allowed_test_hosts) as http,
            closing(WorkerQueuePublisher(settings.valkey_url, namespace, UsageSafetyCaps(), clock)) as publisher,
        ):
            translation = NativeTranslationService(http, settings, database.policies)
            extractor = NativeMediaExtractor()
            browser = LazyBrowserService()
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
                telegram=telegram,
                feedback=AllowFeedback(),
                gate=AllowGate(),
                embed=DisabledEmbedAdapter(),
            )
            outcome = runner.run(envelope.model_dump(mode="json"), JobStage.MEDIA)
            if browser_enabled:
                messages = broker_envelopes(settings, namespace.value, "browser")
                assert len(messages) == 1
                waiting = runtime.admission.get(envelope.job_id)
                assert waiting is not None
                assert waiting.stage == JobStage.BROWSER
                assert waiting.budget_reserved
                outcome = runner.run(messages[0], JobStage.BROWSER)
            assert outcome == expected_outcome.value
            assert len(delivery.calls) == 0
            if expected_notice is not None:
                assert telegram.calls == [(envelope.target.telegram_id, expected_notice)]
                assert PRIVATE_URL not in telegram.calls[0][1]
            if expected_outcome == JobOutcome.FAILED:
                assert runner.run(envelope.model_dump(mode="json"), JobStage.MEDIA) == JobOutcome.FAILED.value
                assert len(telegram.calls) == 1

            ticket = runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == TicketState.TERMINAL
            assert ticket.outcome == expected_outcome

            subject = AccountingSubject(envelope.target.kind, envelope.target.telegram_id)
            total = database.ledger.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), int(clock.now()))
            assert total == 0
            if client_type is BrowserRequiredMediaClient:
                assert broker_envelopes(settings, namespace.value, "browser") == []
                assert (
                    runtime.budgets.finish(envelope.job_id, (UnitAmount(UsageUnit.REQUESTS, 0),), clock.now()).state
                    == ReservationState.RELEASED
                )


@pytest.mark.slow
async def test_retained_candidate_roundtrips_through_encrypted_job_store(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """A retained candidate is encrypted, job-scoped, reloadable and deletable."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    payload = RichMediaPayload(
        author_name="Retained",
        author_handle="retained",
        author_url="https://www.instagram.com/",
        content="retained partial",
        footer_text="⚠️ Some media may be missing.",
        original_url="https://www.instagram.com/p/synthetic/",
        media_items=[MediaItem("https://media.example.invalid/a.jpg", is_video=False)],
    )
    async with runtime_cache(test_container) as (_cache, namespace):
        with WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime:
            data = pack_media_payload(payload)
            assert runtime.payloads.store_candidate("retained-job", data, clock.now() + 300)
            loaded = runtime.payloads.load_candidate("retained-job")
            assert loaded is not None
            restored = unpack_media_payload(loaded)
            assert restored.content == "retained partial"
            assert restored.footer_text == "⚠️ Some media may be missing."
            assert [item.url for item in restored.media_items] == ["https://media.example.invalid/a.jpg"]
            runtime.payloads.delete_candidate("retained-job")
            assert runtime.payloads.load_candidate("retained-job") is None


@pytest.mark.parametrize(
    ("state", "retry_delay", "expected_notice"),
    [
        (ReservationState.WAITING, 10, LIMIT_WAIT_NOTICE_TEXT),
        (ReservationState.WAITING, 400, LIMIT_FAILURE_NOTICE_TEXT),
        (ReservationState.IMPOSSIBLE, None, LIMIT_FAILURE_NOTICE_TEXT),
        (ReservationState.EXPIRED, None, TIMEOUT_NOTICE_TEXT),
        (ReservationState.UNAVAILABLE, None, APPLICATION_FAILURE_NOTICE_TEXT),
    ],
)
async def test_denied_budget_reports_limits_separately_from_application_failure(
    test_container: AsyncContainer,
    postgres_database_url: str,
    state: ReservationState,
    retry_delay: int | None,
    expected_notice: str,
) -> None:
    """Explain delayed, rejected and unavailable budget decisions through real admission notice claims."""
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
        telegram = RecordingTelegram()
        allowed_test_hosts = frozenset({"127.0.0.1", "localhost", "synthetic.invalid", "media.example.invalid"})
        with (
            WorkerDatabase(settings, FEATURE_REGISTRY, UsageSafetyCaps()) as database,
            WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime,
            NativeHTTPClient(allowed_hosts=allowed_test_hosts) as http,
        ):
            runner = FeatureJobRunner(
                admission=runtime.admission,
                payloads=runtime.payloads,
                budgets=runtime.budgets,
                policies=database.policies,
                ledger=database.ledger,
                http=http,
                delivery=RecordingDelivery(),
                translation=NativeTranslationService(http, settings, database.policies),
                extractor=NativeMediaExtractor(),
                clock=clock,
                settings=settings,
                registry=FEATURE_REGISTRY,
                browser=LazyBrowserService(),
                publisher=WorkerQueuePublisher(settings.valkey_url, namespace, UsageSafetyCaps(), clock),
                clients=[VideoMediaClient()],
                telegram=telegram,
                feedback=AllowFeedback(),
                gate=AllowGate(),
                embed=DisabledEmbedAdapter(),
            )
            token = secrets.token_hex(16)
            assert runtime.admission.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), token) is not None
            retry_at = clock.now() + retry_delay if retry_delay is not None else None
            runner._handle_denied_reservation(envelope, token, ReservationDecision(state, retry_at))  # noqa: SLF001
            assert telegram.calls == [(envelope.target.telegram_id, expected_notice)]


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


@pytest.mark.parametrize("failure", ["budget_unavailable", "payload_error"])
async def test_claimed_failure_releases_admission_without_refunding_uncertain_budget(
    test_container: AsyncContainer, postgres_database_url: str, failure: str
) -> None:
    """Failed claimed work frees bounded admission while preserving uncertain budget ownership."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    chat_id, generation = await _seed_entities(test_container, 390001, -390001)
    safety = replace(UsageSafetyCaps(), global_pending=1)
    limits = AdmissionLimits(1, 1, safety)
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        sealed = await payloads.seal("failure-job", PRIVATE_BODY, clock.now() + 300)
        envelope = job_envelope(
            sealed.reference,
            clock,
            job_id=sealed.job_id,
            feature="instagram_post",
            target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=-390001),
        )
        admission = ValkeyAdmissionStore(cache, namespace)
        assert (await admission.admit(envelope, sealed, limits, clock.now())).state == AdmissionState.ACCEPTED
        next_payload = await payloads.seal("next-job", PRIVATE_BODY, envelope.deadline)
        next_envelope = envelope.model_copy(
            update={"job_id": next_payload.job_id, "payload_ref": next_payload.reference, "dedupe_key": "b" * 64}
        )
        assert (await admission.admit(next_envelope, next_payload, limits, clock.now())).state == AdmissionState.FULL
        with (
            WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
        ):
            budget_prefix = namespace.value + "budget:"
            if failure == "budget_unavailable":
                usage = database.policies.resolve(
                    envelope.feature, envelope.trigger, envelope.actor_id, group_id=-390001
                )
                usage = replace(
                    usage, policies=tuple(policy for policy in usage.policies if policy.bucket == MEDIA_BUCKET)
                )
                request = BudgetRequest(envelope.job_id, usage, (UnitAmount(UsageUnit.REQUESTS, 1),), envelope.deadline)
                assert runtime.budgets.reserve(request, clock.now(), (0,)).allowed
                record = json.loads(await cache.client.get(budget_prefix + "reservation:" + envelope.job_id))
                await cache.client.hdel(record["quotas"][0]["key"], "used")
            else:
                payload_key = namespace.value + "payload:" + sealed.reference
                metadata = json.loads(await cache.client.get(payload_key))
                metadata["deadline"] = 10**400
                await cache.client.set(payload_key, json.dumps(metadata))
            budget_before = {
                key: await cache.client.dump(key) async for key in cache.client.scan_iter(match=budget_prefix + "*")
            }
            with worker_child_context(settings, namespace=namespace, clock=clock) as runner:
                assert runner.run(envelope.model_dump(mode="json")) == JobOutcome.FAILED.value
            ticket = runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == TicketState.TERMINAL
            assert ticket.outcome == JobOutcome.FAILED
            assert {
                key: await cache.client.dump(key) async for key in cache.client.scan_iter(match=budget_prefix + "*")
            } == budget_before
            assert (
                await admission.admit(next_envelope, next_payload, limits, clock.now())
            ).state == AdmissionState.ACCEPTED
