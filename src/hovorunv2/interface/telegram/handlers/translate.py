"""Telegram handler for reply-based /translate [target] explicit translation requests."""

import html
import json
import uuid
from typing import ClassVar

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionState
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_jobs import (
    MAX_JOB_LIFETIME_SECONDS,
    JobEnvelope,
    JobTarget,
    PayloadPort,
)
from hovorunv2.application.translation.explicit_input import (
    ExplicitTranslationInput,
    parse_translate_command_target,
    translate_action_key,
)
from hovorunv2.application.translation.ports import TranslationScope
from hovorunv2.application.translation.snapshots import TranslationDeliveryState, TranslationSnapshotPort
from hovorunv2.application.translation.translation_rules import (
    resolve_target_language,
    resolve_translation_source,
)
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName, TargetKind
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.handlers.base import BUSY_NOTICE_TEXT, BaseCommand, BusyNoticeGate
from hovorunv2.interface.telegram.i18n import DEFAULT_LOCALE, AudienceI18nManager, get_message

logger = get_logger(__name__)


class TranslateCommand(BaseCommand):
    """Handle explicit /translate [target] commands replying to a message or selected quote."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(
        requires_admin=False,
        requires_approval=True,
        is_toggleable=False,
        is_automatic=False,
    )
    _BUSY_GATE = BusyNoticeGate()

    def __init__(
        self,
        admission: AdmissionService,
        payloads: PayloadPort,
        chats: ChatStore,
        users: UserStore,
        i18n_manager: AudienceI18nManager,
        clock: Clock,
        settings: AppSettings,
        snapshots: TranslationSnapshotPort,
    ) -> None:
        self._admission = admission
        self._payloads = payloads
        self._chats = chats
        self._users = users
        self._i18n_manager = i18n_manager
        self._clock = clock
        self._settings = settings
        self._snapshots = snapshots
        self._busy_gate = self._BUSY_GATE

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.TRANSLATE

    @property
    def trigger(self) -> Command:
        """Aiogram command trigger filter."""
        return Command(CommandName.TRANSLATE)

    async def is_triggered(self, message: Message) -> bool:
        """Return whether the message triggers /translate."""
        return self._is_slash_command_triggered(message, CommandName.TRANSLATE)

    async def _resolve_ui_locale(self, message: Message, actor_id: int | None) -> str:
        if message.chat.id < 0:
            return await self._i18n_manager.get_chat_locale(message.chat.id) or DEFAULT_LOCALE
        if actor_id is not None:
            return await self._i18n_manager.get_user_locale(actor_id)
        return DEFAULT_LOCALE

    async def _resolve_actor(self, message: Message) -> tuple[int, int] | None:
        if message.sender_chat or message.from_user is None or message.from_user.is_bot:
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

    async def _resolve_target(
        self, message: Message, actor_id: int, actor_generation: int
    ) -> tuple[JobTarget, int, TargetKind, int, str | None] | None:
        if message.chat.id < 0:
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
            return target, chat.generation, TargetKind.USER, actor_id, chat.target_lang

        user = await self._users.get_by_telegram_id(actor_id)
        if user is None:
            return None
        chat = await self._chats.get_chat(message.chat.id)
        chat_target = chat.target_lang if chat is not None else None
        target = JobTarget(
            kind=TargetKind.USER,
            entity_id=user.id,
            generation=actor_generation,
            telegram_id=user.telegram_id,
        )
        return target, actor_generation, TargetKind.USER, user.telegram_id, chat_target

    def _extract_scoped_source(self, message: Message, reply: Message) -> tuple[str, TranslationScope] | None:
        full_source = reply.text or reply.caption or ""
        selected_quote = message.quote.text if message.quote is not None else None
        if message.quote is not None:
            if not full_source or not message.quote.text.strip():
                return None
            encoded_source = full_source.encode("utf-16-le")
            offset = message.quote.position * 2
            quoted_bytes = message.quote.text.encode("utf-16-le")
            if encoded_source[offset : offset + len(quoted_bytes)] != quoted_bytes:
                return None
        try:
            return resolve_translation_source(full_source, selected_quote)
        except ValueError:
            return None

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Validate reply, quote/source scope, target override, and admit explicit translation."""
        del bot, kwargs
        actor = await self._resolve_actor(message)
        actor_id = actor[0] if actor is not None else None
        locale = await self._resolve_ui_locale(message, actor_id)

        try:
            requested_target = parse_translate_command_target(message.text or "")
        except ValueError:
            raw_arg = " ".join((message.text or "").split()[1:])
            await message.reply(get_message("translation-invalid-target", locale, language=html.escape(raw_arg)))
            return

        reply = message.reply_to_message
        if reply is None or reply.chat.id != message.chat.id or message.message_thread_id != reply.message_thread_id:
            notice = f"{get_message('translation-reply-required', locale)}\n{get_message('translation-help', locale)}"
            await message.reply(notice)
            return

        scoped = self._extract_scoped_source(message, reply)
        if scoped is None:
            await message.reply(get_message("translation-empty-source", locale))
            return

        if actor is None:
            await message.reply(get_message("translation-denied", locale))
            return

        target_info = await self._resolve_target(message, actor[0], actor[1])
        if target_info is None:
            await message.reply(get_message("translation-unavailable", locale))
            return
        await self._admit_translation(
            message=message,
            reply=reply,
            scoped_text=scoped[0],
            scope=scoped[1],
            requested_target=requested_target,
            locale=locale,
            actor=actor,
            target_info=target_info,
        )

    async def _admit_translation(
        self,
        *,
        message: Message,
        reply: Message,
        scoped_text: str,
        scope: TranslationScope,
        requested_target: str | None,
        locale: str,
        actor: tuple[int, int],
        target_info: tuple[JobTarget, int, TargetKind, int, str | None],
    ) -> None:
        target, epoch, charged_kind, charged_id, chat_target = target_info
        effective_target = resolve_target_language(
            requested_target,
            chat_target,
            self._settings.translation_target_lang,
        )
        now = self._clock.now()
        deadline = now + MAX_JOB_LIFETIME_SECONDS
        job_id = uuid.uuid4().hex
        dedupe_key = translate_action_key(
            target.telegram_id,
            reply.message_id,
            scope,
            scoped_text,
            effective_target,
            target.entity_id,
            target.generation,
        )
        prior = await self._snapshots.delivery_state(dedupe_key)
        if prior is not None:
            key = (
                "translation-already-complete"
                if prior == TranslationDeliveryState.COMPLETE
                else "translation-unavailable"
            )
            await message.reply(get_message(key, locale))
            return
        private_input = ExplicitTranslationInput(
            text=scoped_text,
            scope=scope,
            source_chat_id=reply.chat.id,
            source_message_id=reply.message_id,
            reply_to_message_id=reply.message_id,
            message_thread_id=message.message_thread_id,
            requested_target_lang=effective_target,
            ui_locale=locale,
        )
        payload_bytes = json.dumps(private_input.model_dump(mode="json")).encode("utf-8")
        sealed = await self._payloads.seal(job_id, payload_bytes, deadline)
        envelope = JobEnvelope(
            job_id=job_id,
            payload_ref=sealed.reference,
            feature=CommandName.TRANSLATE,
            target=target,
            actor_id=actor[0],
            actor_generation=actor[1],
            trigger=TriggerKind.DIRECT,
            charged_kind=charged_kind,
            charged_telegram_id=charged_id,
            accepted_at=now,
            deadline=deadline,
            dedupe_key=dedupe_key,
            feature_epoch=epoch,
            message_id=reply.message_id,
        )
        result = await self._admission.admit(envelope, sealed)
        await self._reply_admission_outcome(message, envelope.job_id, result.state, locale, now)

    async def _reply_admission_outcome(
        self,
        message: Message,
        job_id: str,
        state: AdmissionState,
        locale: str,
        now: float,
    ) -> None:
        try:
            if state == AdmissionState.ACCEPTED:
                handoff = await self._admission.handoff(job_id)
                if handoff.state == AdmissionState.UNAVAILABLE:
                    await message.reply(get_message("translation-unavailable", locale))
            elif state == AdmissionState.DUPLICATE:
                await message.reply(get_message("translation-progress", locale))
            elif state == AdmissionState.DENIED:
                await message.reply(get_message("translation-denied", locale))
            elif state == AdmissionState.FULL:
                if self._busy_gate.should_notify(message.chat.id, now):
                    await message.reply(BUSY_NOTICE_TEXT)
            elif state == AdmissionState.UNAVAILABLE:
                await message.reply(get_message("translation-unavailable", locale))
        except TelegramAPIError:
            logger.exception("Translation admission feedback unavailable")
