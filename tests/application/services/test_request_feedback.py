"""Request progress feedback: bounded overflow, delayed notice, timeout notice."""

import time

from dishka import AsyncContainer

from hovorunv2.application.access.ban_ports import BanKind, BanStore
from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.jobs.admission import JobStage, NoticeKind
from hovorunv2.application.jobs.feature_job_runner import (
    DELAYED_NOTICE_SECONDS,
    LIMIT_WAIT_NOTICE_TEXT,
    TIMEOUT_NOTICE_TEXT,
    FeatureJobRunner,
)
from hovorunv2.application.jobs.feature_jobs import JobOutcome, JobTarget
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.instagram.embed import DisabledEmbedAdapter
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.usage.budgets import ReservationDecision, ReservationState, RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import (
    LazyBrowserService,
    NativeHTTPClient,
    WorkerQueuePublisher,
    WorkerRuntime,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
from hovorunv2.interface.telegram.handlers.base import BusyNoticeGate
from tests.utils.jobs import PRIVATE_BODY, admit_test_job, job_envelope
from tests.utils.runtime import ManualClock, runtime_cache

GROUP = -440001
TEST_HOSTS = frozenset({"127.0.0.1", "localhost", "synthetic.invalid", "media.example.invalid"})


class AllowGate:
    """Allow one delayed notice for synthetic targets."""

    def claim_delayed(self, target_key: str, ttl: int) -> bool:
        """Return True for the controlled test target."""
        return True


class RecordingTelegram:
    """Capture feedback text without network I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []
        self.replies: list[int | None] = []

    def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> int:
        """Capture one feedback message."""
        self.calls.append((chat_id, text))
        self.replies.append(reply_to_message_id)
        return 1


class RecordingDelivery:
    """Capture media delivery calls without network I/O."""

    def __init__(self) -> None:
        self.calls: list[RichMediaPayload] = []

    def deliver(
        self,
        chat_id: int,
        reply_to_message_id: int,
        payload: RichMediaPayload,
        platform_label: str,
        sender_label: str | None = None,
    ) -> tuple[int, ...]:
        """Capture one delivery payload."""
        self.calls.append(payload)
        return (1,)


def test_busy_gate_bounds_repeated_overflow_notices() -> None:
    """A chat receives at most one overflow notice per cooldown window."""
    gate = BusyNoticeGate(cooldown=30)
    assert gate.should_notify(1, 100)
    assert not gate.should_notify(1, 110)
    assert not gate.should_notify(1, 129)
    assert gate.should_notify(1, 130)
    assert gate.should_notify(2, 100)


async def _approved_group(container: AsyncContainer) -> tuple[int, int]:
    chats = await container.get(ChatStore)
    chat = await chats.get_or_create_chat(GROUP)
    await chats.update_status(GROUP, ChatStatus.APPROVED, actor=ActorContext.cli(reason="feedback test"))
    return chat.id, chat.generation


def _runner(
    database: WorkerDatabase,
    runtime: WorkerRuntime,
    http: NativeHTTPClient,
    settings: Settings,
    clock: ManualClock,
    namespace: RuntimeNamespace,
    telegram: RecordingTelegram,
) -> FeatureJobRunner:
    return FeatureJobRunner(
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
        clients=[],
        telegram=telegram,
        feedback=database.policies,
        gate=AllowGate(),
        embed=DisabledEmbedAdapter(),
    )


async def test_expired_job_sends_one_timeout_notice(test_container: AsyncContainer, postgres_database_url: str) -> None:
    """An expired accepted request sends exactly one timeout notice."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url})
    chat_id, generation = await _approved_group(test_container)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        sealed = await payloads.seal("feedback-expired", PRIVATE_BODY, clock.now() + 300)
        await payloads.put(sealed)
        envelope = job_envelope(
            sealed.reference,
            clock,
            job_id="feedback-expired",
            target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=GROUP),
        )
        await admit_test_job(settings, cache, namespace, clock, envelope)
        telegram = RecordingTelegram()
        with (
            WorkerDatabase(settings, FEATURE_REGISTRY, UsageSafetyCaps()) as database,
            WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime,
            NativeHTTPClient(allowed_hosts=TEST_HOSTS) as http,
        ):
            runner = _runner(database, runtime, http, settings, clock, namespace, telegram)
            clock.value = envelope.deadline + 1
            outcome = runner.run(envelope.model_dump(mode="json"), JobStage.MEDIA)
            assert outcome == JobOutcome.EXPIRED.value
            assert telegram.calls == [(GROUP, TIMEOUT_NOTICE_TEXT)]
            assert runner.run(envelope.model_dump(mode="json"), JobStage.MEDIA) == JobOutcome.EXPIRED.value
            assert len(telegram.calls) == 1


async def test_delayed_job_sends_one_notice_and_skips_banned_target(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Quota waiting sends one delayed notice; a banned target receives none."""
    settings = await test_container.get(Settings)
    settings = settings.model_copy(update={"database_url": postgres_database_url})
    chat_id, generation = await _approved_group(test_container)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        sealed = await payloads.seal("feedback-delayed", PRIVATE_BODY, clock.now() + 300)
        await payloads.put(sealed)
        envelope = job_envelope(
            sealed.reference,
            clock,
            job_id="feedback-delayed",
            target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=GROUP),
        )
        await admit_test_job(settings, cache, namespace, clock, envelope)
        telegram = RecordingTelegram()
        with (
            WorkerDatabase(settings, FEATURE_REGISTRY, UsageSafetyCaps()) as database,
            WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime,
            NativeHTTPClient(allowed_hosts=TEST_HOSTS) as http,
        ):
            runner = _runner(database, runtime, http, settings, clock, namespace, telegram)
            token = "t" * 32
            assert runtime.admission.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), token) is not None
            waiting = ReservationDecision(ReservationState.WAITING, clock.now() + DELAYED_NOTICE_SECONDS + 1)
            runner._handle_denied_reservation(envelope, token, waiting)  # noqa: SLF001
            assert telegram.calls == [(GROUP, LIMIT_WAIT_NOTICE_TEXT)]
            assert telegram.replies == [envelope.message_id]
            runner._notify(envelope, NoticeKind.DELAYED, LIMIT_WAIT_NOTICE_TEXT)  # noqa: SLF001
            assert len(telegram.calls) == 1
    await (await test_container.get(BanStore)).set_ban(
        TargetKind.GROUP, GROUP, BanKind.FULL, active=True, actor=ActorContext.cli(reason="ban")
    )
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        sealed = await payloads.seal("feedback-banned", PRIVATE_BODY, clock.now() + 300)
        await payloads.put(sealed)
        envelope = job_envelope(
            sealed.reference,
            clock,
            job_id="feedback-banned",
            target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=GROUP),
        )
        await admit_test_job(settings, cache, namespace, clock, envelope)
        telegram = RecordingTelegram()
        with (
            WorkerDatabase(settings, FEATURE_REGISTRY, UsageSafetyCaps()) as database,
            WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime,
            NativeHTTPClient(allowed_hosts=TEST_HOSTS) as http,
        ):
            runner = _runner(database, runtime, http, settings, clock, namespace, telegram)
            clock.value = envelope.deadline + 1
            runner.run(envelope.model_dump(mode="json"), JobStage.MEDIA)
            assert telegram.calls == []
