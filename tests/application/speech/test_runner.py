"""Exercise real local inference, authority, delivery uncertainty and durable accounting."""

import asyncio
import json
import time
from pathlib import Path
from threading import Event

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionLimits, TicketState
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, JobOutcome, JobTarget
from hovorunv2.application.speech.ports import SpeechSettingsStore
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import SpeechMode, TargetKind
from hovorunv2.domain.usage_accounting import UsageAccountingEventDB
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.runtime.cache import CacheService
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
from tests.utils.jobs import job_envelope
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.speech import USER, private_speech_target
from tests.utils.speech_execution import speech_execution
from tests.utils.sync_http import HTTPReply, json_reply


async def _admitted_speech(
    settings: Settings,
    target: PermissionTarget,
    cache: CacheService,
    namespace: RuntimeNamespace,
    clock: ManualClock,
    *,
    job_id: str = "speech-case",
) -> JobEnvelope:
    assert target.entity_id is not None
    payload = json.dumps(
        {
            "file_id": "synthetic-private-file",
            "message_thread_id": None,
            "source_chat_id": USER,
            "source_message_id": 456,
            "claimed_bytes": 10000,
            "claimed_seconds": 10,
        }
    ).encode()
    sealed = await ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps()).seal(
        job_id, payload, clock.now() + 120
    )
    envelope = job_envelope(
        sealed.reference,
        clock,
        job_id=job_id,
        feature="transcribe",
        target=JobTarget(
            kind=TargetKind.USER, entity_id=target.entity_id, generation=target.generation, telegram_id=USER
        ),
        actor_id=USER,
        actor_generation=target.generation,
        charged_kind=TargetKind.USER,
        charged_telegram_id=USER,
        message_id=456,
        deadline=clock.now() + 120,
    )
    result = await ValkeyAdmissionStore(cache, namespace).admit(
        envelope, sealed, AdmissionLimits(10, 10, UsageSafetyCaps()), clock.now()
    )
    assert result.ticket is not None
    return envelope


@pytest.mark.parametrize("silence", [False, True])
async def test_real_speech_delivery_is_accounted_once(test_container: AsyncContainer, silence: bool) -> None:
    """Native Ukrainian speech consumes once; native silence consumes zero; duplicates never resend."""
    target = await private_speech_target(test_container)
    await (await test_container.get(SpeechSettingsStore)).set_mode(
        target, SpeechMode.MANUAL_ONLY, actor=ActorContext.cli(), transport_kind=None
    )
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    filename = "silence.ogg" if silence else "speech_uk.ogg"
    audio = (Path("tests/utils") / filename).read_bytes()
    async with runtime_cache(test_container) as (cache, namespace):
        envelope = await _admitted_speech(settings, target, cache, namespace, clock)
        with speech_execution(settings, namespace, clock, audio) as execution:
            assert (
                await asyncio.to_thread(execution.runner.run, envelope.model_dump(mode="json")) == JobOutcome.COMPLETE
            )
            ticket = execution.runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == TicketState.TERMINAL
            assert (
                await asyncio.to_thread(execution.runner.run, envelope.model_dump(mode="json")) == JobOutcome.COMPLETE
            )
            sent = [request for request in execution.server.requests if request.path.endswith("/sendMessage")]
            assert len(sent) == 1
            assert b"allow_sending_without_reply" in sent[0].body
            assert b"synthetic-private-file" not in sent[0].body
    engine = await test_container.get(AsyncEngine)
    async with engine.connect() as connection:
        receipt = await connection.scalar(
            select(UsageAccountingEventDB.event_id).where(UsageAccountingEventDB.event_id == envelope.job_id)
        )
    assert (receipt is None) is silence


async def test_unselected_speech_never_downloads_or_recognizes(test_container: AsyncContainer, tmp_path: Path) -> None:
    """Queued work in Off mode is cancelled before any Telegram file request or inference."""
    target = await private_speech_target(test_container)
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        envelope = await _admitted_speech(settings, target, cache, namespace, clock)
        with speech_execution(settings, namespace, clock, b"unread", missing_model=tmp_path) as execution:
            assert execution.runner.run(envelope.model_dump(mode="json")) == JobOutcome.CANCELLED
            assert execution.server.requests == []
            ticket = execution.runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == TicketState.TERMINAL


@pytest.mark.parametrize("uncertain", [True, False])
async def test_uncertain_send_stays_held_and_rejection_settles_zero(
    test_container: AsyncContainer, uncertain: bool
) -> None:
    """An ambiguous response cannot release reservations or trigger automatic transcript replay."""
    target = await private_speech_target(test_container)
    await (await test_container.get(SpeechSettingsStore)).set_mode(
        target, SpeechMode.MANUAL_ONLY, actor=ActorContext.cli(), transport_kind=None
    )
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    reply = (
        HTTPReply(disconnect=True)
        if uncertain
        else json_reply({"ok": False, "error_code": 400, "description": "source message unavailable"})
    )
    audio = await asyncio.to_thread(Path("tests/utils/silence.ogg").read_bytes)
    async with runtime_cache(test_container) as (cache, namespace):
        envelope = await _admitted_speech(settings, target, cache, namespace, clock)
        with speech_execution(settings, namespace, clock, audio, reply=reply) as execution:
            expected = TicketState.DELIVERY_UNCERTAIN if uncertain else JobOutcome.FAILED
            assert await asyncio.to_thread(execution.runner.run, envelope.model_dump(mode="json")) == expected
            ticket = execution.runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == (TicketState.DELIVERY_UNCERTAIN if uncertain else TicketState.TERMINAL)
            await asyncio.to_thread(execution.runner.run, envelope.model_dump(mode="json"))
            assert len([request for request in execution.server.requests if request.path.endswith("/sendMessage")]) == 1


async def test_actor_generation_is_rechecked_before_download(test_container: AsyncContainer, tmp_path: Path) -> None:
    """Deleting an accepted actor prevents file access and output even if the queued reference survives."""
    target = await private_speech_target(test_container)
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    async with runtime_cache(test_container) as (cache, namespace):
        envelope = await _admitted_speech(settings, target, cache, namespace, clock)
        await (await test_container.get(UserStore)).soft_delete_user(USER, actor=ActorContext.cli(), reason="synthetic")
        with speech_execution(settings, namespace, clock, b"unread", missing_model=tmp_path) as execution:
            assert execution.runner.run(envelope.model_dump(mode="json")) == JobOutcome.CANCELLED
            assert execution.server.requests == []


async def test_mode_revocation_during_download_prevents_inference_and_delivery(test_container: AsyncContainer) -> None:
    """Revoked queued authority stops in-flight work and settles zero without revealing any transcript."""
    target = await private_speech_target(test_container)
    store = await test_container.get(SpeechSettingsStore)
    await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.cli(), transport_kind=None)
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    audio = await asyncio.to_thread(Path("tests/utils/speech_uk.ogg").read_bytes)
    started = Event()
    response = HTTPReply(body=audio, started=started, delay_seconds=0.5)
    async with runtime_cache(test_container) as (cache, namespace):
        envelope = await _admitted_speech(settings, target, cache, namespace, clock)
        with speech_execution(settings, namespace, clock, audio, audio_reply=response) as execution:
            task = asyncio.create_task(asyncio.to_thread(execution.runner.run, envelope.model_dump(mode="json")))
            assert await asyncio.to_thread(started.wait, 10)
            await store.set_mode(target, SpeechMode.OFF, actor=ActorContext.cli(), transport_kind=None)
            assert await task == JobOutcome.CANCELLED
            ticket = execution.runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == TicketState.TERMINAL
            assert not any(
                request.path.endswith(("/sendMessage", "/sendDocument")) for request in execution.server.requests
            )


async def test_acknowledgement_after_deadline_still_settles_one_delivery(test_container: AsyncContainer) -> None:
    """A confirmed send begun in time retains a valid immutable receipt even if acknowledgement arrives late."""
    target = await private_speech_target(test_container)
    store = await test_container.get(SpeechSettingsStore)
    await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.cli(), transport_kind=None)
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    audio = await asyncio.to_thread(Path("tests/utils/speech_uk.ogg").read_bytes)
    started = Event()
    reply = HTTPReply(
        body=json.dumps({"ok": True, "result": {"message_id": 901}}).encode(),
        started=started,
        delay_seconds=0.5,
    )
    async with runtime_cache(test_container) as (cache, namespace):
        envelope = await _admitted_speech(settings, target, cache, namespace, clock)
        with speech_execution(settings, namespace, clock, audio, reply=reply) as execution:
            task = asyncio.create_task(asyncio.to_thread(execution.runner.run, envelope.model_dump(mode="json")))
            reached_delivery = await asyncio.to_thread(started.wait, 120)
            if not reached_delivery:
                await task
            assert reached_delivery
            clock.value = envelope.deadline + 1
            assert await task == JobOutcome.COMPLETE
            ticket = execution.runtime.admission.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == TicketState.TERMINAL
            assert ticket.settlement is not None
            assert ticket.settlement.event is not None
            assert ticket.settlement.event.occurred_at <= envelope.deadline
