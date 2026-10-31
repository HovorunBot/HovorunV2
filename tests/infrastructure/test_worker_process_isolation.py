"""Real native worker restart, replacement, and queue-isolation contracts."""

import time

import pytest
from dishka import AsyncContainer

from hovorunv2.application.budgets import RuntimeNamespace
from hovorunv2.application.feature_jobs import EnqueueState, JobEnvelope, JobOutcome, JobTarget
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.feature_queue import CeleryFeatureQueue
from tests.utils.jobs import PRIVATE_BODY, admit_test_job, job_envelope, wait_for_terminal
from tests.utils.native_worker import native_feature_worker
from tests.utils.runtime import ManualClock, runtime_cache

TERMINAL_OUTCOMES = {JobOutcome.COMPLETE, JobOutcome.FAILED}


async def _seed_entities(test_container: AsyncContainer) -> tuple[int, int]:
    """Create the current user and approved group expected by the worker policy read."""
    actor = ActorContext.cli(reason="synthetic worker isolation")
    users = await test_container.get(UserStore)
    chats = await test_container.get(ChatStore)
    await users.ingest_user(390001, actor=actor)
    chat = await chats.get_or_create_chat(-390001, actor=actor)
    await chats.update_status(-390001, ChatStatus.APPROVED, actor=actor)
    return chat.id, chat.generation


async def _admit(
    settings: Settings,
    cache: CacheService,
    namespace: RuntimeNamespace,
    clock: ManualClock,
    job_id: str,
    chat_id: int,
    generation: int,
) -> JobEnvelope:
    """Admit one real encrypted ticket; the payload is only readable by the worker child."""
    payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
    sealed = await payloads.seal(job_id, PRIVATE_BODY, clock.now() + 300)
    envelope = job_envelope(
        sealed.reference,
        clock,
        job_id=job_id,
        dedupe_key=job_id.encode().hex().ljust(64, "0"),
        target=JobTarget(kind=TargetKind.GROUP, entity_id=chat_id, generation=generation, telegram_id=-390001),
    )
    await admit_test_job(settings, cache, namespace, clock, envelope)
    return envelope


@pytest.mark.subprocess
async def test_worker_completes_admitted_work_without_bot_and_restarts_cleanly(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """A media worker drains admitted work while no bot exists, then a fresh worker drains more."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    chat_id, generation = await _seed_entities(test_container)
    async with runtime_cache(test_container) as (cache, namespace):
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            first = await _admit(settings, cache, namespace, clock, "restart-first", chat_id, generation)
            async with native_feature_worker(settings, namespace, (namespace.value + "media",)) as worker:
                first_child = await worker.child_pid()
                assert await queue.enqueue(first) == EnqueueState.ENQUEUED
                terminal = await wait_for_terminal(ValkeyAdmissionStore(cache, namespace), first.job_id)
                assert terminal.envelope == first
                assert terminal.outcome in TERMINAL_OUTCOMES

            second = await _admit(settings, cache, namespace, clock, "restart-second", chat_id, generation)
            async with native_feature_worker(settings, namespace, (namespace.value + "media",)) as worker:
                assert await worker.child_pid() != first_child
                assert await queue.enqueue(second) == EnqueueState.ENQUEUED
                terminal = await wait_for_terminal(ValkeyAdmissionStore(cache, namespace), second.job_id)
                assert terminal.envelope == second
                assert terminal.outcome in TERMINAL_OUTCOMES
        finally:
            await queue.close()


@pytest.mark.subprocess
async def test_sigkilled_prefork_child_is_replaced_and_keeps_exclusive_claim(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """A killed worker child is replaced; the next broker delivery still owns exactly one claim."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    chat_id, generation = await _seed_entities(test_container)
    async with runtime_cache(test_container) as (cache, namespace):
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        try:
            async with native_feature_worker(settings, namespace, (namespace.value + "media",)) as worker:
                first_child = await worker.child_pid()
                await worker.kill_child()
                replacement = await worker.wait_for_new_child(first_child)
                assert replacement != first_child

                envelope = await _admit(settings, cache, namespace, clock, "restart-after-kill", chat_id, generation)
                assert await queue.enqueue(envelope) == EnqueueState.ENQUEUED
                terminal = await wait_for_terminal(ValkeyAdmissionStore(cache, namespace), envelope.job_id)
                assert terminal.envelope == envelope
                assert terminal.outcome in TERMINAL_OUTCOMES
        finally:
            await queue.close()
