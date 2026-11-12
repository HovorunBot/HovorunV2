"""Integration tests for /transcribe command and automatic speech recognition ingress."""

import json
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User, Voice
from dishka import AsyncContainer

from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionPort
from hovorunv2.application.jobs.feature_jobs import PayloadPort
from hovorunv2.application.speech.policies import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    speech_action_key,
)
from hovorunv2.application.speech.ports import SpeechSettingsStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName, SpeechMode, TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.speech import SpeechVoiceHandler, TranscribeCommand
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.speech import GROUP, OTHER, USER, group_speech_target, private_speech_target
from tests.utils.telegram import BOT_CREDENTIAL, RecordedResponse
from tests.utils.telegram.session import RecordingBot, RecordingSession

ANONYMOUS_SENDER_ID = 1087968824


def _send_message_payload(message_id: int) -> dict[str, object]:
    return {
        "ok": True,
        "result": {"message_id": message_id, "date": 1, "chat": {"id": GROUP, "type": "supergroup"}},
    }


def _create_voice_message(
    *,
    chat_id: int = GROUP,
    message_id: int = 100,
    user_id: int = OTHER,
    is_bot: bool = False,
    file_id: str = "voice_file_123",
    duration: int = 30,
    file_size: int = 50_000,
    thread_id: int | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="supergroup" if chat_id < 0 else "private"),
        from_user=User(id=user_id, first_name="Speaker", is_bot=is_bot),
        voice=Voice(file_id=file_id, file_unique_id=f"u_{file_id}", duration=duration, file_size=file_size),
        message_thread_id=thread_id,
    )


def _create_command_message(
    *,
    chat_id: int = GROUP,
    message_id: int = 101,
    user_id: int = USER,
    reply_to: Message | None = None,
    text: str = "/transcribe",
    thread_id: int | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="supergroup" if chat_id < 0 else "private"),
        from_user=User(id=user_id, first_name="Requester", is_bot=False),
        text=text,
        reply_to_message=reply_to,
        message_thread_id=thread_id,
    )


async def test_transcribe_validation_rejections(test_container: AsyncContainer) -> None:
    """Manual /transcribe cleanly rejects invalid reply contexts and media bounds."""
    await group_speech_target(test_container)
    (await test_container.get(Settings)).speech_enabled = True
    handler = await test_container.get(TranscribeCommand)
    bot = Bot("777:synthetic-token")

    # 1. No reply message
    msg_no_reply = _create_command_message(reply_to=None)
    with RecordedResponses() as responses:
        responses.post(
            "https://api.telegram.org/bot777:synthetic-token/sendMessage",
            payload={"ok": True, "result": {"message_id": 1, "date": 1, "chat": {"id": GROUP, "type": "supergroup"}}},
            response_class=RecordedResponse,
        )
        await handler.handle(msg_no_reply.as_(bot), bot)
        assert len(responses.requests) == 1

    # 2. Reply to non-audio (text)
    text_reply = Message(
        message_id=99,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP, type="supergroup"),
        from_user=User(id=OTHER, first_name="Speaker", is_bot=False),
        text="Hello world",
    )
    msg_text = _create_command_message(reply_to=text_reply)
    with RecordedResponses() as responses:
        responses.post(
            "https://api.telegram.org/bot777:synthetic-token/sendMessage",
            payload={"ok": True, "result": {"message_id": 2, "date": 1, "chat": {"id": GROUP, "type": "supergroup"}}},
            response_class=RecordedResponse,
        )
        await handler.handle(msg_text.as_(bot), bot)
        assert len(responses.requests) == 1

    # 3. Reply to bot message
    bot_reply = _create_voice_message(is_bot=True)
    msg_bot = _create_command_message(reply_to=bot_reply)
    with RecordedResponses() as responses:
        responses.post(
            "https://api.telegram.org/bot777:synthetic-token/sendMessage",
            payload={"ok": True, "result": {"message_id": 3, "date": 1, "chat": {"id": GROUP, "type": "supergroup"}}},
            response_class=RecordedResponse,
        )
        await handler.handle(msg_bot.as_(bot), bot)
        assert len(responses.requests) == 1

    # 4. Reply to audio with oversized duration (>120s)
    long_audio = _create_voice_message(duration=MAX_AUDIO_SECONDS + 10)
    msg_long = _create_command_message(reply_to=long_audio)
    with RecordedResponses() as responses:
        responses.post(
            "https://api.telegram.org/bot777:synthetic-token/sendMessage",
            payload={"ok": True, "result": {"message_id": 4, "date": 1, "chat": {"id": GROUP, "type": "supergroup"}}},
            response_class=RecordedResponse,
        )
        await handler.handle(msg_long.as_(bot), bot)
        assert len(responses.requests) == 1

    # 5. Reply to audio with oversized file size (>10MB)
    huge_audio = _create_voice_message(file_size=MAX_AUDIO_BYTES + 100)
    msg_huge = _create_command_message(reply_to=huge_audio)
    with RecordedResponses() as responses:
        responses.post(
            "https://api.telegram.org/bot777:synthetic-token/sendMessage",
            payload={"ok": True, "result": {"message_id": 5, "date": 1, "chat": {"id": GROUP, "type": "supergroup"}}},
            response_class=RecordedResponse,
        )
        await handler.handle(msg_huge.as_(bot), bot)
        assert len(responses.requests) == 1

    await bot.session.close()


async def test_transcribe_mode_matrix_and_admission(test_container: AsyncContainer) -> None:
    """Manual /transcribe checks mode, admits valid jobs, and detects duplicates."""
    target = await group_speech_target(test_container)
    settings_store = await test_container.get(SpeechSettingsStore)
    (await test_container.get(Settings)).speech_enabled = True
    handler = await test_container.get(TranscribeCommand)
    admission_port = await test_container.get(AdmissionPort)
    payload_port = await test_container.get(PayloadPort)
    bot = Bot("777:synthetic-token")

    voice_msg = _create_voice_message(message_id=200, duration=15, file_size=30_000, thread_id=5)
    cmd_msg = _create_command_message(message_id=201, reply_to=voice_msg, thread_id=5)

    try:
        # Case 1: SpeechMode is OFF -> rejection notice
        assert await settings_store.mode(target) == SpeechMode.OFF
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/sendMessage",
                payload=_send_message_payload(1),
                response_class=RecordedResponse,
            )
            await handler.handle(cmd_msg.as_(bot), bot)
            assert len(responses.requests) == 1

        # Case 2: Set SpeechMode to MANUAL_ONLY -> accepted and admitted
        await settings_store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.cli(), transport_kind=None)
        assert await settings_store.mode(target) == SpeechMode.MANUAL_ONLY

        await handler.handle(cmd_msg.as_(bot), bot)

        # Verify job was admitted in admission port
        expected_dedupe = speech_action_key(GROUP, voice_msg.message_id, target.entity_id, target.generation)
        active_tickets = await admission_port.pending_for_feature(CommandName.TRANSCRIBE)
        assert len(active_tickets) >= 1
        ticket = next(t for t in active_tickets if t.envelope.message_id == voice_msg.message_id)
        assert ticket.envelope.feature == CommandName.TRANSCRIBE
        assert ticket.envelope.trigger == TriggerKind.DIRECT
        assert ticket.envelope.dedupe_key == expected_dedupe

        # Verify sealed payload contains file_id and message_thread_id
        loaded = await payload_port.load(ticket.envelope.payload_ref, ticket.envelope.job_id)
        assert loaded.data is not None
        payload_data = json.loads(loaded.data.decode("utf-8"))
        assert payload_data["file_id"] == "voice_file_123"
        assert payload_data["message_thread_id"] == 5

        # Case 3: Duplicate invocation on same voice message -> duplicate reply
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/sendMessage",
                payload=_send_message_payload(2),
                response_class=RecordedResponse,
            )
            cmd_msg2 = _create_command_message(message_id=202, reply_to=voice_msg, thread_id=5)
            await handler.handle(cmd_msg2.as_(bot), bot)
            assert len(responses.requests) == 1
    finally:
        await bot.session.close()


async def test_automatic_capture_matrix_and_shared_dedupe(test_container: AsyncContainer) -> None:
    """Automatic capture admits only in MANUAL_AND_AUTOMATIC mode and shares dedupe with /transcribe."""
    target = await group_speech_target(test_container)
    settings_store = await test_container.get(SpeechSettingsStore)
    (await test_container.get(Settings)).speech_enabled = True
    voice_handler = await test_container.get(SpeechVoiceHandler)
    transcribe_cmd = await test_container.get(TranscribeCommand)
    admission_port = await test_container.get(AdmissionPort)
    bot = Bot("777:synthetic-token")

    voice_msg = _create_voice_message(message_id=300, duration=20, file_size=40_000)

    try:
        # Case 1: Mode is OFF -> automatic capture ignored
        assert await settings_store.mode(target) == SpeechMode.OFF
        await voice_handler.handle(voice_msg.as_(bot), bot)
        tickets = [
            t
            for t in await admission_port.pending_for_feature(CommandName.TRANSCRIBE_AUTO)
            if t.envelope.message_id == 300
        ]
        assert len(tickets) == 0

        # Case 2: Mode is MANUAL_ONLY -> automatic capture still ignored
        await settings_store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.cli(), transport_kind=None)
        await voice_handler.handle(voice_msg.as_(bot), bot)
        tickets = [
            t
            for t in await admission_port.pending_for_feature(CommandName.TRANSCRIBE_AUTO)
            if t.envelope.message_id == 300
        ]
        assert len(tickets) == 0

        # Case 3: Mode is MANUAL_AND_AUTOMATIC -> automatic capture admits job!
        await settings_store.set_mode(
            target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.cli(), transport_kind=None
        )
        await voice_handler.handle(voice_msg.as_(bot), bot)
        tickets = [
            t
            for t in await admission_port.pending_for_feature(CommandName.TRANSCRIBE_AUTO)
            if t.envelope.message_id == 300
        ]
        assert len(tickets) == 1
        ticket = tickets[0]
        assert ticket.envelope.feature == CommandName.TRANSCRIBE_AUTO
        assert ticket.envelope.trigger == TriggerKind.AUTOMATIC

        # Case 4: Subsequent manual /transcribe replying to same voice message is detected as DUPLICATE
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/sendMessage",
                payload=_send_message_payload(302),
                response_class=RecordedResponse,
            )
            manual_cmd = _create_command_message(message_id=301, reply_to=voice_msg)
            await transcribe_cmd.handle(manual_cmd.as_(bot), bot)
            assert len(responses.requests) == 1
    finally:
        await bot.session.close()


async def test_private_automatic_capture_and_invariants(test_container: AsyncContainer) -> None:
    """Private automatic capture admits job bound to the private actor and charges them."""
    target = await private_speech_target(test_container)
    settings_store = await test_container.get(SpeechSettingsStore)
    (await test_container.get(Settings)).speech_enabled = True
    voice_handler = await test_container.get(SpeechVoiceHandler)
    admission_port = await test_container.get(AdmissionPort)
    bot = Bot("777:synthetic-token")

    await settings_store.set_mode(
        target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.cli(), transport_kind=None
    )

    private_voice = _create_voice_message(chat_id=USER, message_id=400, user_id=USER, duration=10, file_size=20_000)
    try:
        await voice_handler.handle(private_voice.as_(bot), bot)
        tickets = [
            t
            for t in await admission_port.pending_for_feature(CommandName.TRANSCRIBE_AUTO)
            if t.envelope.message_id == 400
        ]
        assert len(tickets) == 1
        ticket = tickets[0]
        assert ticket.envelope.feature == CommandName.TRANSCRIBE_AUTO
        assert ticket.envelope.trigger == TriggerKind.AUTOMATIC
        assert ticket.envelope.target.kind == TargetKind.USER
        assert ticket.envelope.charged_kind == TargetKind.USER
        assert ticket.envelope.actor_id == USER
    finally:
        await bot.session.close()


async def test_disabled_worker_never_admits_manual_or_automatic_audio(test_container: AsyncContainer) -> None:
    """An operator-disabled worker yields manual feedback and queues no private input."""
    target = await group_speech_target(test_container)
    settings_store = await test_container.get(SpeechSettingsStore)
    await settings_store.set_mode(
        target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.cli(), transport_kind=None
    )
    (await test_container.get(Settings)).speech_enabled = False
    admission = await test_container.get(AdmissionPort)
    before = await _speech_job_ids(admission)
    voice = _create_voice_message(message_id=550)
    async with RecordingBot(BOT_CREDENTIAL, session=RecordingSession()) as bot:
        await (await test_container.get(SpeechVoiceHandler)).handle(voice.as_(bot), bot)
        command = _create_command_message(reply_to=voice)
        await (await test_container.get(TranscribeCommand)).handle(command.as_(bot), bot)
        assert len(bot.session.calls) == 1
    assert await _speech_job_ids(admission) == before


async def test_manual_source_requires_exact_topic(test_container: AsyncContainer) -> None:
    """A topic reply cannot move a source from the general chat into another topic."""
    await group_speech_target(test_container)
    (await test_container.get(Settings)).speech_enabled = True
    handler = await test_container.get(TranscribeCommand)
    admission = await test_container.get(AdmissionPort)
    before = await _speech_job_ids(admission)
    async with RecordingBot(BOT_CREDENTIAL, session=RecordingSession()) as bot:
        command = _create_command_message(reply_to=_create_voice_message(), thread_id=5)
        await handler.handle(command.as_(bot), bot)
        assert len(bot.session.calls) == 1
    assert await _speech_job_ids(admission) == before


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("source_kind", ["human", "bot", "anonymous", "channel"])
async def test_speech_distinguishes_chat_senders_from_actual_bots(
    test_container: AsyncContainer, source_kind: str, *, manual: bool
) -> None:
    """Accept chat-owned voice sources without attributing them to a compatibility bot user."""
    target = await group_speech_target(test_container)
    await (await test_container.get(SpeechSettingsStore)).set_mode(
        target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.cli(), transport_kind=None
    )
    (await test_container.get(Settings)).speech_enabled = True
    chat_sender = source_kind in {"anonymous", "channel"}
    source = _create_voice_message(
        message_id=600,
        user_id=ANONYMOUS_SENDER_ID if chat_sender else OTHER,
        is_bot=source_kind != "human",
    )
    if chat_sender:
        sender = source.chat if source_kind == "anonymous" else Chat(id=GROUP - 1, type="channel")
        source = source.model_copy(update={"sender_chat": sender})
    feature = CommandName.TRANSCRIBE if manual else CommandName.TRANSCRIBE_AUTO
    async with RecordingBot(BOT_CREDENTIAL, session=RecordingSession()) as bot:
        if manual:
            command = _create_command_message(reply_to=source)
            await (await test_container.get(TranscribeCommand)).handle(command.as_(bot), bot)
        else:
            handler = await test_container.get(SpeechVoiceHandler)
            assert await handler.is_triggered(source) == (source_kind != "bot")
            await handler.handle(source.as_(bot), bot)
    tickets = await (await test_container.get(AdmissionPort)).pending_for_feature(feature)
    if source_kind == "bot":
        assert not tickets
        return
    assert len(tickets) == 1
    envelope = tickets[0].envelope
    assert envelope.message_id == source.message_id
    if manual:
        assert envelope.actor_id == USER
        assert envelope.charged_kind == TargetKind.USER
    elif chat_sender:
        assert envelope.actor_id is None
        assert envelope.actor_generation is None
        assert envelope.charged_kind == TargetKind.GROUP
    assert await (await test_container.get(UserStore)).get_by_telegram_id(ANONYMOUS_SENDER_ID) is None


async def _speech_job_ids(admission: AdmissionPort) -> set[str]:
    tickets = await admission.pending_for_feature(CommandName.TRANSCRIBE)
    tickets += await admission.pending_for_feature(CommandName.TRANSCRIBE_AUTO)
    return {ticket.envelope.job_id for ticket in tickets}
