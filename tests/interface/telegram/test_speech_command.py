"""Integration tests for /transcribe command and automatic speech recognition ingress."""

import json
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.types import Chat, Message, User, Voice
from dishka import AsyncContainer

from hovorunv2.application.features.registry import TriggerKind
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
from hovorunv2.interface.telegram.handlers.speech import SpeechVoiceHandler, TranscribeCommand
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.speech import GROUP, OTHER, USER, group_speech_target, private_speech_target
from tests.utils.telegram import RecordedResponse


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
