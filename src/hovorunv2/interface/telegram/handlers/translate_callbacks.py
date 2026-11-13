"""Telegram callback handler for source-bound Translate and Continue translation buttons."""

import json
import logging
import uuid
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.features.feature_access_service import FeatureAccessService
from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionState
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_jobs import MAX_JOB_LIFETIME_SECONDS, JobEnvelope, JobTarget, PayloadPort
from hovorunv2.application.translation.explicit_input import ExplicitTranslationInput, translate_action_key
from hovorunv2.application.translation.snapshots import (
    CHAIN_CALLBACK_PREFIX,
    SOURCE_CALLBACK_PREFIX,
    TOKEN_PATTERN,
    TranslationChainState,
    TranslationSnapshotPort,
    TranslationSourceSnapshot,
)
from hovorunv2.application.translation.translation_rules import resolve_target_language
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName, TargetKind
from hovorunv2.interface.telegram.i18n import DEFAULT_LOCALE, AudienceI18nManager, get_message

logger = logging.getLogger(__name__)
_CHAIN_PART_COUNT = 2


def _parse_source_token(data: str) -> str | None:
    if not data.startswith(SOURCE_CALLBACK_PREFIX):
        return None
    token = data.removeprefix(SOURCE_CALLBACK_PREFIX)
    return token if TOKEN_PATTERN.fullmatch(token) is not None else None


def _parse_chain_callback(data: str) -> tuple[str, int] | None:
    if not data.startswith(CHAIN_CALLBACK_PREFIX):
        return None
    rest = data.removeprefix(CHAIN_CALLBACK_PREFIX)
    parts = rest.split(":", maxsplit=1)
    if len(parts) != _CHAIN_PART_COUNT:
        return None
    token, raw_idx = parts
    if TOKEN_PATTERN.fullmatch(token) is None or not raw_idx.isdigit():
        return None
    idx = int(raw_idx)
    return (token, idx) if idx >= 1 else None


class TranslateCallbackHandler:
    """Handle source-bound Translate and Continue translation inline button callbacks."""

    def __init__(
        self,
        snapshots: TranslationSnapshotPort,
        admission: AdmissionService,
        payloads: PayloadPort,
        features: FeatureAccessService,
        users: UserStore,
        chats: ChatStore,
        i18n_manager: AudienceI18nManager,
        clock: Clock,
        settings: AppSettings,
    ) -> None:
        self._snapshots = snapshots
        self._admission = admission
        self._payloads = payloads
        self._features = features
        self._users = users
        self._chats = chats
        self._i18n_manager = i18n_manager
        self._clock = clock
        self._settings = settings

    async def _resolve_ui_locale(self, chat_id: int, user_id: int) -> str:
        if chat_id < 0:
            return await self._i18n_manager.get_chat_locale(chat_id) or DEFAULT_LOCALE
        return await self._i18n_manager.get_user_locale(user_id)

    async def _is_authorized(self, chat_id: int, owner_id: int | None, actor_id: int) -> bool:
        if chat_id > 0:
            return owner_id == actor_id
        decision = await self._features.evaluate(
            CommandName.TRANSLATE,
            chat_id,
            actor_id,
            trigger=TriggerKind.DIRECT,
        )
        return decision.allowed

    async def handle_callback(self, query: CallbackQuery, bot: Bot) -> None:
        """Dispatch Translate and Continue translation callbacks."""
        if query.data is None or not isinstance(query.message, Message) or query.from_user.is_bot:
            await query.answer()
            return
        source_token = _parse_source_token(query.data)
        if source_token is not None:
            await self._handle_source_click(query, query.message, source_token)
            return
        chain_parsed = _parse_chain_callback(query.data)
        if chain_parsed is not None:
            await self._handle_chain_click(query, query.message, bot, chain_parsed[0], chain_parsed[1])
            return
        await query.answer()

    async def _handle_source_click(self, query: CallbackQuery, msg: Message, token: str) -> None:
        actor_id = query.from_user.id
        locale = await self._resolve_ui_locale(msg.chat.id, actor_id)
        snapshot = await self._snapshots.get_source(token)
        if snapshot is None:
            await query.answer(get_message("translation-expired", locale), show_alert=True)
            return
        if snapshot.chat_id != msg.chat.id or not await self._is_authorized(
            snapshot.chat_id, snapshot.owner_id, actor_id
        ):
            await query.answer(get_message("translation-denied", locale), show_alert=True)
            return
        await self._admit_snapshot(query, msg, snapshot, locale)

    async def _admit_snapshot(
        self, query: CallbackQuery, msg: Message, snapshot: TranslationSourceSnapshot, locale: str
    ) -> None:
        actor_id = query.from_user.id
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None:
            user = await self._users.ingest_user(actor_id, actor=ActorContext.system("telegram_identity_evidence"))
        if user is None:
            await query.answer(get_message("translation-denied", locale), show_alert=True)
            return
        target, epoch, chat_target = await self._resolve_target(snapshot.chat_id, user.id, user.generation)
        if target is None:
            await query.answer(get_message("translation-unavailable", locale), show_alert=True)
            return
        effective_target = resolve_target_language(None, chat_target, self._settings.translation_target_lang)
        now = self._clock.now()
        deadline = now + MAX_JOB_LIFETIME_SECONDS
        job_id = uuid.uuid4().hex
        private_input = ExplicitTranslationInput(
            text=snapshot.text,
            scope=snapshot.scope,
            source_chat_id=snapshot.chat_id,
            source_message_id=snapshot.source_message_id,
            reply_to_message_id=msg.message_id,
            message_thread_id=snapshot.thread_id if snapshot.thread_id is not None else msg.message_thread_id,
            requested_target_lang=None,
            ui_locale=locale,
            snapshot_expires_at=snapshot.expires_at,
        )
        payload_bytes = json.dumps(private_input.model_dump(mode="json")).encode("utf-8")
        sealed = await self._payloads.seal(job_id, payload_bytes, deadline)
        dedupe_key = translate_action_key(
            target.telegram_id,
            snapshot.source_message_id,
            snapshot.scope,
            snapshot.text,
            effective_target,
            target.entity_id,
            target.generation,
        )
        envelope = JobEnvelope(
            job_id=job_id,
            payload_ref=sealed.reference,
            feature=CommandName.TRANSLATE,
            target=target,
            actor_id=user.telegram_id,
            actor_generation=user.generation,
            trigger=TriggerKind.DIRECT,
            charged_kind=TargetKind.USER,
            charged_telegram_id=user.telegram_id,
            accepted_at=now,
            deadline=deadline,
            dedupe_key=dedupe_key,
            feature_epoch=epoch,
            message_id=snapshot.source_message_id,
        )
        result = await self._admission.admit(envelope, sealed)
        await self._answer_admission(query, envelope.job_id, result.state, locale)

    async def _resolve_target(
        self, chat_id: int, user_entity_id: int, user_generation: int
    ) -> tuple[JobTarget | None, int, str | None]:
        if chat_id < 0:
            chat = await self._chats.get_chat(chat_id)
            if chat is None:
                return None, 0, None
            target = JobTarget(
                kind=TargetKind.GROUP,
                entity_id=chat.id,
                generation=chat.generation,
                telegram_id=chat.chat_id,
            )
            return target, chat.generation, chat.target_lang
        chat = await self._chats.get_chat(chat_id)
        chat_target = chat.target_lang if chat is not None else None
        user_target = JobTarget(
            kind=TargetKind.USER,
            entity_id=user_entity_id,
            generation=user_generation,
            telegram_id=chat_id,
        )
        return user_target, user_generation, chat_target

    async def _answer_admission(self, query: CallbackQuery, job_id: str, state: AdmissionState, locale: str) -> None:
        if state == AdmissionState.ACCEPTED:
            handoff = await self._admission.handoff(job_id)
            if handoff.state == AdmissionState.UNAVAILABLE:
                await query.answer(get_message("translation-unavailable", locale), show_alert=True)
                return
            await query.answer(get_message("translation-progress", locale))
            return
        if state == AdmissionState.DUPLICATE:
            await query.answer(get_message("translation-progress", locale))
            return
        if state == AdmissionState.DENIED:
            await query.answer(get_message("translation-denied", locale), show_alert=True)
            return
        await query.answer(get_message("translation-unavailable", locale), show_alert=True)

    async def _handle_chain_click(
        self, query: CallbackQuery, msg: Message, bot: Bot, token: str, part_idx: int
    ) -> None:
        actor_id = query.from_user.id
        locale = await self._resolve_ui_locale(msg.chat.id, actor_id)
        if not await self._snapshots.claim_chain_step(token, part_idx):
            await query.answer()
            return
        try:
            chain = await self._snapshots.get_chain(token)
            if chain is None:
                await query.answer(get_message("translation-expired", locale), show_alert=True)
                return
            if (
                chain.chat_id != msg.chat.id
                or chain.last_message_id != msg.message_id
                or not await self._is_authorized(chain.chat_id, chain.owner_id, actor_id)
            ):
                await query.answer(get_message("translation-denied", locale), show_alert=True)
                return
            if part_idx != chain.next_part:
                notice = (
                    get_message("translation-already-complete", locale) if chain.next_part >= len(chain.parts) else ""
                )
                await query.answer(notice or None)
                return
            await self._deliver_next_part(query, bot, token, chain, part_idx, locale)
        finally:
            await self._snapshots.release_chain_step(token, part_idx)

    async def _deliver_next_part(
        self,
        query: CallbackQuery,
        bot: Bot,
        token: str,
        chain: TranslationChainState,
        part_idx: int,
        locale: str,
    ) -> None:
        next_idx = part_idx + 1
        has_more = next_idx < len(chain.parts)
        markup = None
        if has_more:
            label = get_message("translation-btn-continue", chain.ui_locale)
            remaining = get_message("translation-remaining-parts", chain.ui_locale, count=len(chain.parts) - next_idx)
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{label} {remaining}".strip(),
                            callback_data=f"tr:c:{token}:{next_idx}",
                        )
                    ]
                ]
            )
        sent = await bot.send_message(
            chat_id=chain.chat_id,
            text=chain.parts[part_idx],
            parse_mode="HTML",
            reply_to_message_id=chain.last_message_id,
            message_thread_id=chain.thread_id,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        with suppress(TelegramAPIError):
            await bot.edit_message_reply_markup(
                chat_id=chain.chat_id,
                message_id=chain.last_message_id,
                reply_markup=None,
            )
        updated = chain.model_copy(update={"next_part": next_idx, "last_message_id": sent.message_id})
        await self._snapshots.save_chain(token, updated)
        await query.answer(None if has_more else get_message("translation-already-complete", locale))

    def register(self, router: Router) -> None:
        """Register translation callback handler on the Telegram router."""
        router.callback_query.register(
            self.handle_callback,
            F.data.startswith("tr:"),
            flags={"requires_group_admin": False},
        )
