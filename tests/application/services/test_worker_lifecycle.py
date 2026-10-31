"""Real Valkey/PostgreSQL crash recovery, fair waiting, and conservative terminal fencing."""

import asyncio
import json
import multiprocessing
import secrets
import time
from contextlib import closing

import pytest
from celery import Celery
from dishka import AsyncContainer

from hovorunv2.application.admission import (
    RECOVERY_RETRY_SECONDS,
    AdmissionLimits,
    AdmissionTicket,
    AdmissionUnavailableError,
    JobStage,
    NoticeKind,
    SettlementIntent,
    TicketState,
)
from hovorunv2.application.budgets import BudgetRequest, ReservationState
from hovorunv2.application.feature_jobs import JobEnvelope, JobOutcome, JobTarget, PayloadState
from hovorunv2.application.features import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.ports import UserStore
from hovorunv2.application.services.worker_lifecycle_service import WorkerLifecycleService
from hovorunv2.application.usage import UsageSafetyCaps, UsageUnit, UsageWindow
from hovorunv2.application.usage_ledger import AccountingSubject
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import WorkerQueuePublisher, WorkerRuntime, worker_child_context
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.admission_state import admission_keys
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.feature_queue import RECOVERY_TASK_FUNCTION
from tests.utils.celery_recovery import native_recovery_worker
from tests.utils.identities import FEATURE, FEATURE_BUCKET, ONE, OTHER_FEATURE, ZERO
from tests.utils.jobs import PRIVATE_BODY, PRIVATE_URL, broker_envelopes, job_envelope
from tests.utils.lifecycle import crash_at_settlement_checkpoint
from tests.utils.runtime import ManualClock, RuntimeNamespace, runtime_cache

OWNER = "synthetic-owner"


async def _admit(
    store: ValkeyAdmissionStore,
    payloads: ValkeyPayloadStore,
    clock: ManualClock,
    job_id: str,
    **changes: object,
) -> JobEnvelope:
    sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
    envelope = job_envelope(
        sealed.reference, clock, job_id=job_id, dedupe_key=job_id.encode().hex().ljust(64, "0"), **changes
    )
    result = await store.admit(envelope, sealed, AdmissionLimits(25, 25, UsageSafetyCaps()), clock.now())
    assert result.ticket is not None
    return envelope


@pytest.mark.subprocess
@pytest.mark.parametrize("checkpoint", ["prepared", "receipt", "runtime", "unavailable"])
async def test_child_crash_converges_after_each_accounting_phase(
    test_container: AsyncContainer,
    postgres_database_url: str,
    checkpoint: str,
) -> None:
    """A fresh native process reconciles an abruptly exited child's immutable settlement exactly once."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    users = await test_container.get(UserStore)
    await users.ingest_user(390001, actor=ActorContext.cli(reason="synthetic recovery"))
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        envelope = await _admit(store, payloads, clock, "crash-job", feature=FEATURE)
        process = multiprocessing.get_context("spawn").Process(
            target=crash_at_settlement_checkpoint,
            args=(settings, namespace, envelope, checkpoint),
        )
        process.start()
        try:
            await asyncio.to_thread(process.join, 20)
            assert not process.is_alive()
            assert process.exitcode == 0
        finally:
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 5)
            process.close()
        ticket = await store.get(envelope.job_id)
        assert ticket is not None
        assert ticket.state == TicketState.SETTLING
        assert (await payloads.load(envelope.payload_ref, envelope.job_id)).state == PayloadState.READY
        with (
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
            WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
        ):
            lifecycle = WorkerLifecycleService(runtime.admission, runtime.budgets, database.ledger, clock)
            if checkpoint == "unavailable":
                await _interrupt_runtime_backend(cache, namespace, envelope, lifecycle, ticket)
            lifecycle.reconcile(ticket)
            terminal = runtime.admission.get(envelope.job_id)
            assert terminal is not None
            assert terminal.outcome == JobOutcome.COMPLETE
            assert terminal.terminal_notice_requested
            assert runtime.admission.claim_notice(envelope.job_id, NoticeKind.TERMINAL)
            assert not await store.claim_notice(envelope.job_id, NoticeKind.TERMINAL)
            assert not runtime.admission.claim_notice(envelope.job_id, NoticeKind.DELAYED)
            # Replaying the old checkpoint must neither charge again nor renew terminal retention.
            ttl_before = await cache.client.pttl(admission_keys(namespace.value, envelope).ticket)
            lifecycle.reconcile(ticket)
            assert await cache.client.pttl(admission_keys(namespace.value, envelope).ticket) <= ttl_before
            subject = AccountingSubject(envelope.charged_kind, envelope.charged_telegram_id)
            assert (
                database.ledger.total(subject, FEATURE_BUCKET, UsageUnit.REQUESTS, UsageWindow(), int(clock.now())) == 1
            )
            assert runtime.budgets.finish(envelope.job_id, ONE, clock.now()).state == ReservationState.COMMITTED
        keys = admission_keys(namespace.value, envelope)
        assert await cache.client.hget(keys.totals, "count") == b"0"
        assert await cache.client.hget(keys.totals, "bytes") == b"0"
        assert (await payloads.load(envelope.payload_ref, envelope.job_id)).state == PayloadState.MISSING
        assert PRIVATE_URL not in json.dumps(ticket.settlement.model_dump(mode="json"))


@pytest.mark.parametrize("automatic", [True, False])
async def test_fair_rotation_preserves_each_subject_provider_lane(
    test_container: AsyncContainer, *, automatic: bool
) -> None:
    """A flooded subject/provider cannot hide another lane, including when sweeps have a one-ticket bound."""
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        changes: dict[str, object] = {}
        if automatic:
            changes = {
                "trigger": TriggerKind.AUTOMATIC,
                "charged_kind": TargetKind.GROUP,
                "charged_telegram_id": -390001,
            }
        flooded = [await _admit(store, payloads, clock, f"flood-{index}", **changes) for index in range(10)]
        other_changes = {**changes, "actor_id": 390002, "charged_telegram_id": -390002 if automatic else 390002}
        if automatic:
            other_changes["target"] = JobTarget(kind=TargetKind.GROUP, entity_id=2, generation=1, telegram_id=-390002)
        other = await _admit(store, payloads, clock, "other", **other_changes)
        provider = await _admit(store, payloads, clock, "provider", feature=OTHER_FEATURE, **changes)
        with WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime:
            for envelope in (*flooded, other, provider):
                assert runtime.admission.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), "owner") is not None
                assert runtime.admission.defer(envelope.job_id, "owner", clock.now() + 10) is not None
            page = runtime.admission.recovery_page()
            assert len(page) == 3
            assert {other.job_id, provider.job_id} <= {ticket.envelope.job_id for ticket in page}
            visited = {runtime.admission.recovery_page(1)[0].envelope.job_id for _ in range(30)}
            assert {item.job_id for item in (*flooded, other, provider)} <= visited
            ticket = runtime.admission.get(other.job_id)
            assert ticket is not None
            assert ticket.delayed_notice_requested
            assert ticket.envelope == other
            assert runtime.admission.claim_notice(other.job_id, NoticeKind.DELAYED)
            assert not await store.claim_notice(other.job_id, NoticeKind.DELAYED)
            assert not runtime.admission.claim_notice(other.job_id, NoticeKind.TERMINAL)
            assert runtime.admission.claim_stage(other.job_id, JobStage.MEDIA, clock.now(), "early") is None
            clock.value += 10
            assert runtime.admission.claim_stage(other.job_id, JobStage.MEDIA, clock.now(), "eligible") is not None
            assert not runtime.admission.finish(other.job_id, JobOutcome.COMPLETE, token=secrets.token_hex(16))


async def test_recovery_waits_without_publishing_and_expires_queued_work(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Controlled time releases waiting via real broker publication; original deadlines still expire queued work."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        envelope = await _admit(store, payloads, clock, "waiting-job")
        publisher = WorkerQueuePublisher(settings.valkey_url, namespace, safety, clock)
        with (
            closing(publisher),
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
            WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
        ):
            lifecycle = WorkerLifecycleService(runtime.admission, runtime.budgets, database.ledger, clock)
            assert runtime.admission.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), "owner")
            assert runtime.admission.defer(envelope.job_id, "owner", clock.now() + 10)
            assert lifecycle.recover(publisher) == 0
            assert not await asyncio.to_thread(broker_envelopes, settings, namespace.value)
            clock.value += 10
            assert lifecycle.recover(publisher) == 1
            messages = await asyncio.to_thread(broker_envelopes, settings, namespace.value)
            assert messages == [envelope.model_dump(mode="json")]
            assert lifecycle.recover(publisher) == 0
            clock.value += RECOVERY_RETRY_SECONDS
            assert lifecycle.recover(publisher) == 1
            assert await asyncio.to_thread(broker_envelopes, settings, namespace.value) == messages
            clock.value = envelope.deadline
            assert lifecycle.recover(publisher) == 1
            terminal = runtime.admission.get(envelope.job_id)
            assert terminal is not None
            assert terminal.outcome == JobOutcome.EXPIRED
            assert terminal.envelope == envelope
            assert runtime.admission.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), "expired") is None
            assert (await payloads.load(envelope.payload_ref, envelope.job_id)).state == PayloadState.MISSING


def _reserve(
    runtime: WorkerRuntime,
    database: WorkerDatabase,
    envelope: JobEnvelope,
    clock: ManualClock,
) -> None:
    """Acquire a real budget before testing owner, continuation, and terminal fences."""
    usage = database.policies.resolve(envelope.feature, envelope.trigger, envelope.actor_id)
    request = BudgetRequest(envelope.job_id, usage, ONE, envelope.deadline)
    assert runtime.budgets.reserve(request, clock.now(), tuple(0 for _ in usage.policies)).allowed
    assert runtime.admission.mark_reserved(envelope.job_id, OWNER) is not None


@pytest.mark.parametrize("state", [TicketState.RUNNING, TicketState.CANCELLING, TicketState.DELIVERY_UNCERTAIN])
async def test_expiry_never_refunds_live_cancelled_or_uncertain_execution(
    test_container: AsyncContainer,
    postgres_database_url: str,
    state: TicketState,
) -> None:
    """Elapsed deadlines and repeated recovery cannot prove child/browser execution or delivery stopped."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    users = await test_container.get(UserStore)
    await users.ingest_user(390001, actor=ActorContext.cli(reason="synthetic fencing"))
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        envelope = await _admit(store, payloads, clock, "active-job", feature=FEATURE)
        publisher = WorkerQueuePublisher(settings.valkey_url, namespace, safety, clock)
        with (
            closing(publisher),
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
            WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
        ):
            lifecycle = WorkerLifecycleService(runtime.admission, runtime.budgets, database.ledger, clock)
            assert runtime.admission.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), OWNER)
            _reserve(runtime, database, envelope, clock)
            if state == TicketState.CANCELLING:
                assert runtime.admission.cancel(envelope.job_id)
                assert runtime.admission.begin_delivery(envelope.job_id, OWNER, clock.now()) is None
            if state == TicketState.DELIVERY_UNCERTAIN:
                assert runtime.admission.begin_delivery(envelope.job_id, OWNER, clock.now())
                assert not runtime.admission.cancel(envelope.job_id)
            clock.value = envelope.deadline + 1
            assert lifecycle.recover(publisher) == 0
            assert lifecycle.recover(publisher) == 0
            assert runtime.admission.finish(envelope.job_id, JobOutcome.EXPIRED) is None
            assert runtime.admission.finish(envelope.job_id, JobOutcome.FAILED, token=OWNER) is None
            ticket = runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == state
            keys = admission_keys(namespace.value, envelope)
            assert await cache.client.hget(keys.totals, "count") == b"1"
            assert await cache.client.exists(keys.payload)
            assert not await asyncio.to_thread(broker_envelopes, settings, namespace.value)
            if state == TicketState.CANCELLING:
                lifecycle.complete(envelope.job_id, OWNER, SettlementIntent(outcome=JobOutcome.CANCELLED, actual=ZERO))
                assert await cache.client.hget(keys.totals, "count") == b"0"


@pytest.mark.parametrize("cancel", [True, False])
async def test_browser_queued_cancellation_and_expiry_settle_before_release(
    test_container: AsyncContainer,
    postgres_database_url: str,
    *,
    cancel: bool,
) -> None:
    """A browser handoff retains its budget until verified queued termination can reconcile zero consumption."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    users = await test_container.get(UserStore)
    await users.ingest_user(390001, actor=ActorContext.cli(reason="synthetic continuation"))
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        envelope = await _admit(store, payloads, clock, "browser-job", feature=FEATURE)
        publisher = WorkerQueuePublisher(settings.valkey_url, namespace, safety, clock)
        with (
            closing(publisher),
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
            WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
        ):
            lifecycle = WorkerLifecycleService(runtime.admission, runtime.budgets, database.ledger, clock)
            assert runtime.admission.claim_stage(envelope.job_id, JobStage.MEDIA, clock.now(), OWNER)
            _reserve(runtime, database, envelope, clock)
            assert runtime.admission.handoff_stage(envelope.job_id, OWNER, JobStage.BROWSER)
            if cancel:
                assert runtime.admission.cancel(envelope.job_id)
                ticket = runtime.admission.get(envelope.job_id)
                assert ticket is not None
                assert ticket.state == TicketState.SETTLING
                assert (await payloads.load(envelope.payload_ref, envelope.job_id)).state == PayloadState.READY
            else:
                clock.value = envelope.deadline
                assert runtime.admission.claim_stage(envelope.job_id, JobStage.BROWSER, clock.now(), "expired") is None
            assert lifecycle.recover(publisher) == 1
            terminal = runtime.admission.get(envelope.job_id)
            assert terminal is not None
            assert terminal.outcome == (JobOutcome.CANCELLED if cancel else JobOutcome.EXPIRED)
            assert terminal.envelope == envelope
            assert runtime.budgets.finish(envelope.job_id, ZERO, clock.now()).state == ReservationState.RELEASED
            assert lifecycle.recover(publisher) == 0
            assert (await payloads.load(envelope.payload_ref, envelope.job_id)).state == PayloadState.MISSING


async def _interrupt_runtime_backend(
    cache: CacheService,
    namespace: RuntimeNamespace,
    envelope: JobEnvelope,
    lifecycle: WorkerLifecycleService,
    ticket: AdmissionTicket,
) -> None:
    """Simulate a missing budget backend resource across a durable receipt commit, then restore it."""
    budget = await cache.get(namespace.value + "budget:reservation:" + envelope.job_id)
    quota_key = budget["quotas"][0]["key"]
    saved = await cache.client.dump(quota_key)
    ttl = await cache.client.pttl(quota_key)
    await cache.client.delete(quota_key)
    with pytest.raises(AdmissionUnavailableError):
        lifecycle.reconcile(ticket)
    assert await cache.client.hget(admission_keys(namespace.value, envelope).totals, "count") == b"1"
    current = await ValkeyAdmissionStore(cache, namespace).get(envelope.job_id)
    assert current is not None
    assert current.state == TicketState.SETTLING
    await cache.client.restore(quota_key, ttl, saved)


@pytest.mark.subprocess
async def test_real_celery_prefork_recovery_task_expires_queued_ticket(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """A broker-dispatched operations task runs recovery in an actual synchronous Celery prefork child."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time() - 310)
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        envelope = await _admit(store, payloads, clock, "celery-expiry")
        async with native_recovery_worker(settings, namespace, cache):
            with closing(Celery("probe_publisher", broker=settings.valkey_url)) as app:
                app.conf.broker_transport_options = {"global_keyprefix": namespace.value}
                await asyncio.to_thread(app.send_task, RECOVERY_TASK_FUNCTION, queue=namespace.value + "operations")
            ticket = await _wait_terminal(store, envelope.job_id)
            assert ticket.outcome == JobOutcome.EXPIRED
            assert (await payloads.load(envelope.payload_ref, envelope.job_id)).state == PayloadState.MISSING


async def _wait_terminal(store: ValkeyAdmissionStore, job_id: str) -> AdmissionTicket:
    """Wait for broker execution without substituting Celery's result status for admission facts."""
    async with asyncio.timeout(15):
        while True:
            ticket = await store.get(job_id)
            assert ticket is not None
            if ticket.state == TicketState.TERMINAL:
                return ticket
            await asyncio.sleep(0.05)


async def test_runner_defers_real_quota_denial_without_holding_execution_claim(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Native quota denial persists waiting and returns before extraction or network execution."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    users = await test_container.get(UserStore)
    await users.ingest_user(390001, actor=ActorContext.cli(reason="synthetic waiting"))
    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    async with runtime_cache(test_container) as (cache, namespace):
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        blocker = await _admit(store, payloads, clock, "blocker", feature=FEATURE)
        waiting = await _admit(store, payloads, clock, "waiting", feature=FEATURE)
        with (
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
            WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
            worker_child_context(settings, namespace=namespace, clock=clock) as runner,
        ):
            assert runtime.admission.claim_stage(blocker.job_id, JobStage.MEDIA, clock.now(), OWNER)
            _reserve(runtime, database, blocker, clock)
            assert runner.run(waiting.model_dump(mode="json")) == ReservationState.WAITING.value
            ticket = runtime.admission.get(waiting.job_id)
            assert ticket is not None
            assert ticket.state == TicketState.WAITING
            assert ticket.token is None
            assert ticket.not_before > clock.now()
            assert ticket.envelope == waiting
            assert ticket.delayed_notice_requested
            assert not await cache.client.exists(namespace.value + "budget:reservation:" + waiting.job_id)
            clock.value = ticket.not_before
            assert runner.run(waiting.model_dump(mode="json")) == ReservationState.WAITING.value
            assert runtime.admission.cancel(waiting.job_id)
            lifecycle = WorkerLifecycleService(runtime.admission, runtime.budgets, database.ledger, clock)
            lifecycle.complete(blocker.job_id, OWNER, SettlementIntent(outcome=JobOutcome.CANCELLED, actual=ZERO))
