"""Telegram handlers for manual /transcribe command and automatic speech recognition."""

import json
import uuid
from typing import ClassVar

from aiogram import Bot, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Audio, Message, Voice
from aiogram.utils.magic_filter import MagicFilter

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionState
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_jobs import (
    APPLICATION_FAILURE_NOTICE_TEXT,
    MAX_JOB_LIFETIME_SECONDS,
    JobEnvelope,
    JobTarget,
    PayloadPort,
)
from hovorunv2.application.speech.input_data import SpeechInput
from hovorunv2.application.speech.policies import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    speech_action_key,
)
from hovorunv2.application.speech.ports import SpeechSettingsStore
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName, SpeechMode, TargetKind
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.base import BUSY_NOTICE_TEXT, BaseCommand, BusyNoticeGate

logger = get_logger(__name__)


class BaseSpeechHandler(BaseCommand):
    """Base class providing target resolution and job admission for speech recognition."""

    _BUSY_GATE = BusyNoticeGate()

    def __init__(
        self,
        admission: AdmissionService,
        payloads: PayloadPort,
        chats: ChatStore,
        users: UserStore,
        speech_settings: SpeechSettingsStore,
        clock: Clock,
        settings: AppSettings,
    ) -> None:
        """Initialize handler dependencies."""
        self._admission = admission
        self._payloads = payloads
        self._chats = chats
        self._users = users
        self._speech_settings = speech_settings
        self._settings = settings
        self._clock = clock
        self._busy_gate = self._BUSY_GATE

    async def _resolve_sender(self, message: Message) -> tuple[int | None, int | None] | None:
        """Resolve numeric actor, generation,  from Telegram message.

        Args:
            message: Telegram message carrying the sender.

        Returns:
            Actor ID and generation, or None when sender is absent or bot.
        """
        if message.sender_chat:
            return None, None
        if message.from_user is None or message.from_user.is_bot:
            return None
        user = await self._users.get_by_telegram_id(message.from_user.id)
        if user is None:
            user = await self._users.ingest_user(
                message.from_user.id,
                actor=ActorContext.system("telegram_identity_evidence"),
            )
        if user is None:
            return None
        return user.telegram_id, user.generation

    async def _resolve_group_target(
        self, message: Message, actor_id: int | None, *, direct: bool
    ) -> tuple[JobTarget, int, TriggerKind, TargetKind, int] | None:
        """Resolve group chat target, epoch, and charging configuration.

        Args:
            message: Telegram message carrying the group context.
            actor_id: Numeric actor ID, or None when sender is a channel.
            direct: Whether the interaction was a direct command.

        Returns:
            Target tuple or None when unavailable.
        """
        chat = await self._chats.get_chat(message.chat.id)
        if chat is None:
            chat = await self._chats.get_or_create_chat(
                message.chat.id,
                actor=ActorContext.system("telegram_identity_evidence"),
            )
        if chat is None:
            return None
        target = JobTarget(
            kind=TargetKind.GROUP,
            entity_id=chat.id,
            generation=chat.generation,
            telegram_id=chat.chat_id,
        )
        if direct:
            if actor_id is None:
                return None
            return target, chat.generation, TriggerKind.DIRECT, TargetKind.USER, actor_id
        return target, chat.generation, TriggerKind.AUTOMATIC, TargetKind.GROUP, chat.chat_id

    async def _resolve_target_and_charging(
        self,
        message: Message,
        actor_id: int | None,
        actor_generation: int | None,
        *,
        direct: bool,
    ) -> tuple[JobTarget, int, TriggerKind, TargetKind, int] | None:
        """Resolve target, epoch, and charge parameters for group or private chat.

        Args:
            message: Telegram message carrying the chat context.
            actor_id: Numeric actor ID, or None when absent.
            actor_generation: Current actor generation, or None when absent.
            direct: Whether the interaction was a direct command.

        Returns:
            Resolved target tuple or None when unavailable.
        """
        if message.chat.id < 0:
            return await self._resolve_group_target(message, actor_id, direct=direct)
        if actor_id is None or actor_generation is None:
            return None
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None:
            return None
        target = JobTarget(
            kind=TargetKind.USER,
            entity_id=user.id,
            generation=user.generation,
            telegram_id=user.telegram_id,
        )
        trigger = TriggerKind.DIRECT if direct else TriggerKind.AUTOMATIC
        return target, user.generation, trigger, TargetKind.USER, user.telegram_id

    async def _reply_application_failure(self, message: Message) -> None:
        """Send bounded failure feedback without leaking internal details."""
        if not self._busy_gate.should_notify(message.chat.id, self._clock.now()):
            return
        try:
            await message.reply(APPLICATION_FAILURE_NOTICE_TEXT)
        except TelegramAPIError:
            logger.exception("Speech admission failure feedback unavailable")

    async def _admit_speech_job(
        self,
        *,
        message: Message,
        source_message: Message,
        file_id: str,
        thread_id: int | None,
        target: JobTarget,
        epoch: int,
        actor_id: int | None,
        actor_generation: int | None,
        trigger: TriggerKind,
        charged_kind: TargetKind,
        charged_id: int,
        feature: str,
    ) -> None:
        """Seal payload, admit speech recognition job and initiate queue handoff."""
        now = self._clock.now()
        deadline = now + MAX_JOB_LIFETIME_SECONDS
        job_id = uuid.uuid4().hex
        media = source_message.voice or source_message.audio
        if media is None or media.file_size is None:
            msg = "Validated speech source requires audio metadata."
            raise ValueError(msg)
        private_input = SpeechInput(
            file_id=file_id,
            message_thread_id=thread_id,
            source_chat_id=source_message.chat.id,
            source_message_id=source_message.message_id,
            claimed_bytes=media.file_size,
            claimed_seconds=media.duration,
        )
        payload_bytes = json.dumps(private_input.model_dump(mode="json")).encode("utf-8")
        sealed = await self._payloads.seal(job_id, payload_bytes, deadline)
        dedupe_key = speech_action_key(
            target.telegram_id, source_message.message_id, target.entity_id, target.generation
        )
        envelope = JobEnvelope(
            job_id=job_id,
            payload_ref=sealed.reference,
            feature=feature,
            target=target,
            actor_id=actor_id,
            actor_generation=actor_generation,
            trigger=trigger,
            charged_kind=charged_kind,
            charged_telegram_id=charged_id,
            accepted_at=now,
            deadline=deadline,
            dedupe_key=dedupe_key,
            feature_epoch=epoch,
            message_id=source_message.message_id,
        )
        result = await self._admission.admit(envelope, sealed)
        if result.state == AdmissionState.ACCEPTED:
            handoff = await self._admission.handoff(envelope.job_id)
            if handoff.state == AdmissionState.UNAVAILABLE and trigger == TriggerKind.DIRECT:
                await self._reply_application_failure(message)
        elif trigger == TriggerKind.DIRECT:
            if result.state == AdmissionState.DUPLICATE:
                await message.reply("⏳ Transcription is already in progress or completed for this message.")
            elif result.state == AdmissionState.FULL:
                if self._busy_gate.should_notify(message.chat.id, now):
                    await message.reply(BUSY_NOTICE_TEXT)
            elif result.state == AdmissionState.UNAVAILABLE:
                await self._reply_application_failure(message)
            else:
                logger.info("Speech admission denied for %s (%s)", feature, result.state.value)


class TranscribeCommand(BaseSpeechHandler):
    """Handle explicit /transcribe command replying to a voice or audio message."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(
        requires_admin=False,
        requires_approval=True,
        is_toggleable=False,
        is_automatic=False,
    )

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.TRANSCRIBE

    @property
    def trigger(self) -> Command:
        """Aiogram command trigger filter."""
        return Command(CommandName.TRANSCRIBE)

    async def is_triggered(self, message: Message) -> bool:
        """Return whether the message triggers the transcribe command."""
        return self._is_slash_command_triggered(message, CommandName.TRANSCRIBE)

    def _validate_reply(self, message: Message, reply: Message | None) -> str | None:
        """Validate reply context and sender, returning error message on rejection."""
        if reply is None:
            return "Reply to a voice or audio message with /transcribe to transcribe it."
        if reply.chat.id != message.chat.id:
            return "Cannot transcribe messages from another chat."
        if message.message_thread_id != reply.message_thread_id:
            return "Cannot transcribe messages from a different topic."
        if reply.from_user and reply.from_user.is_bot:
            return "Cannot transcribe messages sent by bots."
        return None

    def _validate_media(self, media: Audio | Voice | None) -> str | None:
        """Validate audio bounds and presence, returning error message on rejection."""
        if media is None:
            return "Only voice and audio messages are supported for transcription."
        if not media.file_id:
            return "Audio file reference is unavailable."
        return self._validate_media_bounds(media)

    def _validate_media_bounds(self, media: Audio | Voice) -> str | None:
        """Validate audio duration and size limits."""
        if media.duration is None or media.duration <= 0:
            return "Cannot transcribe audio without duration metadata."
        if media.duration > MAX_AUDIO_SECONDS:
            return f"Audio duration ({media.duration}s) exceeds the maximum limit of {MAX_AUDIO_SECONDS}s."
        if media.file_size is None or media.file_size <= 0:
            return "Cannot transcribe audio without file size metadata."
        if media.file_size > MAX_AUDIO_BYTES:
            limit_mb = MAX_AUDIO_BYTES // 1_000_000
            return f"Audio file size exceeds the maximum limit of {limit_mb} MB."
        return None

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Validate input, permissions, metadata bounds, and admit manual transcription.

        Args:
            message: Telegram message carrying the /transcribe command.
            bot: Bot instance bound to the handler.
            **kwargs: Unused extra context.
        """
        if not self._settings.speech_enabled:
            await message.reply("Speech recognition is unavailable on this bot.")
            return
        reply = message.reply_to_message
        reply_error = self._validate_reply(message, reply)
        if reply_error:
            await message.reply(reply_error)
            return

        assert reply is not None
        media = reply.voice or reply.audio
        media_error = self._validate_media(media)
        if media_error or not media:
            await message.reply(media_error or "Only voice and audio messages are supported for transcription.")
            return

        sender = await self._resolve_sender(message)
        if sender is None:
            return
        actor_id, actor_generation = sender

        target_info = await self._resolve_target_and_charging(message, actor_id, actor_generation, direct=True)
        if target_info is None:
            return
        target, epoch, trigger, charged_kind, charged_id = target_info

        perm_target = PermissionTarget(target.kind.value, target.entity_id, target.generation, target.telegram_id)
        mode = await self._speech_settings.mode(perm_target)
        if mode == SpeechMode.OFF:
            await message.reply("Speech recognition is disabled in this chat. Enable it in /settings.")
            return

        thread_id = reply.message_thread_id or message.message_thread_id
        await self._admit_speech_job(
            message=message,
            source_message=reply,
            file_id=media.file_id,
            thread_id=thread_id,
            target=target,
            epoch=epoch,
            actor_id=actor_id,
            actor_generation=actor_generation,
            trigger=trigger,
            charged_kind=charged_kind,
            charged_id=charged_id,
            feature=CommandName.TRANSCRIBE,
        )


class SpeechVoiceHandler(BaseSpeechHandler):
    """Handle automatic capture of voice and audio messages in authorized chats."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(
        requires_admin=False,
        requires_approval=True,
        is_toggleable=False,
        is_automatic=True,
    )

    @property
    def name(self) -> str:
        """Handler feature identifier."""
        return CommandName.TRANSCRIBE_AUTO

    @property
    def trigger(self) -> MagicFilter:
        """Aiogram filter triggering on new voice or audio messages."""
        return (F.voice | F.audio) & (F.sender_chat | ~F.from_user.is_bot)

    async def is_triggered(self, message: Message) -> bool:
        """Return whether the message is a valid voice or audio message."""
        if not (message.voice or message.audio):
            return False
        return not (message.from_user and message.from_user.is_bot)

    def _is_eligible_media(self, media: Audio | Voice | None) -> bool:
        """Return whether the media meets duration, size and metadata bounds."""
        if media is None:
            return False
        if media.duration is None or not (0 < media.duration <= MAX_AUDIO_SECONDS):
            return False
        if media.file_size is None or not (0 < media.file_size <= MAX_AUDIO_BYTES):
            return False
        return bool(media.file_id)

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Validate voice metadata and admit automatic transcription when mode allows.

        Args:
            message: Telegram message carrying voice or audio.
            bot: Bot instance bound to the handler.
            **kwargs: Unused extra context.
        """
        if not self._settings.speech_enabled:
            return
        if message.from_user and message.from_user.is_bot:
            return

        media = message.voice or message.audio
        if not self._is_eligible_media(media) or not media:
            return

        sender = await self._resolve_sender(message)
        if sender is None:
            return
        actor_id, actor_generation = sender

        target_info = await self._resolve_target_and_charging(message, actor_id, actor_generation, direct=False)
        if target_info is None:
            return
        target, epoch, trigger, charged_kind, charged_id = target_info

        perm_target = PermissionTarget(target.kind.value, target.entity_id, target.generation, target.telegram_id)
        if await self._speech_settings.mode(perm_target) != SpeechMode.MANUAL_AND_AUTOMATIC:
            return

        await self._admit_speech_job(
            message=message,
            source_message=message,
            file_id=media.file_id,
            thread_id=message.message_thread_id,
            target=target,
            epoch=epoch,
            actor_id=actor_id,
            actor_generation=actor_generation,
            trigger=trigger,
            charged_kind=charged_kind,
            charged_id=charged_id,
            feature=CommandName.TRANSCRIBE_AUTO,
        )
