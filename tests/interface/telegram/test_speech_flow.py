"""Verify speech ingress, broker handoff, native execution, delivery and persisted accounting together."""

import asyncio
import json
import math
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from aiogram.types import Chat, Message, User, Voice
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import TicketState
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, JobOutcome
from hovorunv2.application.speech.ports import SpeechSettingsStore
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import SpeechMode
from hovorunv2.domain.usage_accounting import UsageAccountingEventDB
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.queues.feature_queue import (
    CeleryFeatureQueue,
    run_speech_job,
    set_feature_task_runner,
)
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.speech.whisper_engine import SAMPLE_RATE, decode_audio_to_pcm
from hovorunv2.interface.telegram.handlers.speech import BaseSpeechHandler, SpeechVoiceHandler, TranscribeCommand
from tests.utils.jobs import admission_service, broker_envelopes
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.speech import GROUP, USER, group_speech_target, private_speech_target
from tests.utils.speech_execution import qualified_model_directory, speech_execution
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.telegram.session import RecordingBot, RecordingSession

pytestmark = pytest.mark.subprocess


async def _handler(
    container: AsyncContainer,
    queue: CeleryFeatureQueue,
    store: ValkeyAdmissionStore,
    payloads: ValkeyPayloadStore,
    clock: ManualClock,
    *,
    manual: bool,
) -> BaseSpeechHandler:
    settings = (await container.get(Settings)).model_copy(update={"speech_enabled": True})
    handler_type = TranscribeCommand if manual else SpeechVoiceHandler
    return handler_type(
        await admission_service(container, store, queue, clock),
        payloads,
        await container.get(ChatStore),
        await container.get(UserStore),
        await container.get(SpeechSettingsStore),
        clock,
        settings,
    )


def _voice_message(chat_id: int, audio: bytes) -> Message:
    pcm = decode_audio_to_pcm(audio)
    return Message(
        message_id=456,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
        from_user=User(id=USER, is_bot=False, first_name="Synthetic"),
        voice=Voice(
            file_id="synthetic-private-file",
            file_unique_id="synthetic-voice",
            duration=math.ceil(len(pcm) / (SAMPLE_RATE * 4)),
            file_size=len(audio),
        ),
        message_thread_id=6 if chat_id < 0 else None,
    )


def _manual_request(source: Message) -> Message:
    return Message(
        message_id=457,
        date=source.date,
        chat=source.chat,
        from_user=source.from_user,
        text="/transcribe",
        reply_to_message=source,
        message_thread_id=source.message_thread_id,
    )


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("private", [False, True])
async def test_voice_ingress_through_broker_delivers_and_charges_once(
    test_container: AsyncContainer, test_engine: AsyncEngine, *, manual: bool, private: bool
) -> None:
    """Preserve source routing and charge exactly once across actual speech admission, broker and native I/O."""
    qualified_model_directory()
    path = Path("tests/utils/speech_uk_short.ogg")
    audio = await asyncio.to_thread(path.read_bytes)
    target = await private_speech_target(test_container) if private else await group_speech_target(test_container)
    await (await test_container.get(SpeechSettingsStore)).set_mode(
        target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.cli(), transport_kind=None
    )
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    source = await asyncio.to_thread(_voice_message, USER if private else GROUP, audio)
    message = _manual_request(source) if manual else source
    async with runtime_cache(test_container) as (cache, namespace), AsyncExitStack() as resources:
        queue = CeleryFeatureQueue(settings, clock, namespace, UsageSafetyCaps())
        resources.push_async_callback(queue.close)
        store = ValkeyAdmissionStore(cache, namespace)
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, UsageSafetyCaps())
        handler = await _handler(test_container, queue, store, payloads, clock, manual=manual)
        async with RecordingBot(BOT_CREDENTIAL, session=RecordingSession()) as bot:
            await handler.handle(message.as_(bot), bot)
            assert not bot.session.calls
        messages = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "speech")
        assert len(messages) == 1
        assert not await asyncio.to_thread(broker_envelopes, settings, namespace.value, "media")
        envelope = JobEnvelope.model_validate(messages[0])
        with speech_execution(settings, namespace, clock, audio) as execution:
            set_feature_task_runner(execution.runner.run)
            try:
                assert await asyncio.to_thread(run_speech_job, messages[0]) == JobOutcome.COMPLETE
                assert await asyncio.to_thread(run_speech_job, messages[0]) == JobOutcome.COMPLETE
            finally:
                set_feature_task_runner(None)
            sent = [request for request in execution.server.requests if request.path.endswith("/sendMessage")]
            assert len(sent) == 1
            fields = parse_qs(sent[0].body.decode())
            assert fields["chat_id"] == [str(target.telegram_id)]
            assert json.loads(fields["reply_parameters"][0]) == {
                "message_id": source.message_id,
                "allow_sending_without_reply": False,
            }
            assert fields["text"][0].startswith("<b>Transcript</b> (uk):")
            if source.message_thread_id is not None:
                assert fields["message_thread_id"] == [str(source.message_thread_id)]
            ticket = await store.get(envelope.job_id)
            assert ticket is not None
            assert ticket.state == TicketState.TERMINAL
            assert ticket.outcome == JobOutcome.COMPLETE
            assert ticket.settlement is not None
            assert ticket.settlement.event is not None
            assert all(charge.amount == 1 for charge in ticket.settlement.event.charges)
    async with test_engine.connect() as connection:
        receipts = (await connection.scalars(select(UsageAccountingEventDB.event_id))).all()
    assert len(receipts) == 1
    assert receipts[0] == envelope.job_id
