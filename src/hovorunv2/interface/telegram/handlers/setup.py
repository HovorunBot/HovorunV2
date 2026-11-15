"""Telegram callback and rendering handler for sequential guided chat configuration."""

import contextlib
from typing import ClassVar, Final

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.data.languages import LANGUAGES, POPULAR_LANGUAGES, resolve_language_code
from hovorunv2.application.features.feature_access_service import FeatureAccessService
from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.settings.setup_apply import SetupApplyOutcome, SetupApplyService
from hovorunv2.application.settings.setup_drafts import (
    _SPEECH_FEATURE_KEYS,
    SetupConfigurationValues,
    SetupDraft,
    SetupDraftService,
    SetupSessionOutcome,
    SetupStep,
)
from hovorunv2.domain.enums import CommandName, SpeechMode
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.i18n.core import DEFAULT_LOCALE, SUPPORTED_LOCALES, get_message
from hovorunv2.interface.telegram.i18n.manager import AudienceI18nManager
from hovorunv2.interface.telegram.settings.callbacks import SetupAction, SetupCallback

_LANGS_PER_PAGE: Final = 8
_UI_LABELS: Final[dict[str, str]] = {
    "uk": "🇺🇦 Українська",
    "en": "🇬🇧 English",
}
_SPEECH_MODES: Final[tuple[SpeechMode, ...]] = (
    SpeechMode.OFF,
    SpeechMode.MANUAL_ONLY,
    SpeechMode.MANUAL_AND_AUTOMATIC,
)


def _ordered_language_codes() -> tuple[str, ...]:
    popular = [code for code in POPULAR_LANGUAGES if code in LANGUAGES]
    rest = sorted(code for code in LANGUAGES if code not in popular)
    return (*popular, *rest)


def _feature_display_label(key: str) -> str:
    feature = FEATURE_REGISTRY.get(key)
    if feature is not None:
        return feature.display_name
    return key


def _format_summary_values(values: SetupConfigurationValues, locale: str) -> dict[str, str]:
    ui_code = values.ui_locale or DEFAULT_LOCALE
    ui_label = _UI_LABELS.get(ui_code, ui_code)
    tgt_code = values.target_lang or "uk"
    tgt_lang = LANGUAGES.get(tgt_code)
    tgt_label = tgt_lang.display_name if tgt_lang else tgt_code
    visible_ignored = [c for c in (values.ignored_langs or ()) if c != "und"]
    if visible_ignored:
        ignored_label = ", ".join(LANGUAGES[c].display_name if c in LANGUAGES else c for c in visible_ignored)
    else:
        ignored_label = get_message("setup-none", locale)
    features_label = (
        ", ".join(_feature_display_label(k) for k in values.enabled_features)
        if values.enabled_features
        else get_message("setup-none", locale)
    )
    speech_mode = values.speech_mode or SpeechMode.OFF
    speech_label = get_message(f"settings-speech-mode-{speech_mode.value}", locale)
    return {
        "ui_locale": ui_label,
        "target_lang": tgt_label,
        "ignored": ignored_label,
        "features": features_label,
        "speech_mode": speech_label,
    }


def _build_nav_row(
    builder: InlineKeyboardBuilder,
    session_id: str,
    step: SetupStep,
    locale: str,
) -> None:
    nav: list[InlineKeyboardButton] = []
    if step != SetupStep.UI_LOCALE:
        nav.append(
            InlineKeyboardButton(
                text=get_message("setup-btn-back", locale),
                callback_data=SetupCallback(action=SetupAction.BACK, session_id=session_id).pack(),
            )
        )
    if step == SetupStep.REVIEW:
        nav.append(
            InlineKeyboardButton(
                text=get_message("setup-btn-apply", locale),
                callback_data=SetupCallback(action=SetupAction.APPLY, session_id=session_id).pack(),
            )
        )
    else:
        nav.append(
            InlineKeyboardButton(
                text=get_message("setup-btn-next", locale),
                callback_data=SetupCallback(action=SetupAction.NEXT, session_id=session_id, value=step.value).pack(),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=get_message("setup-btn-cancel", locale),
            callback_data=SetupCallback(action=SetupAction.CANCEL, session_id=session_id).pack(),
        )
    )
    builder.row(*nav)


def render_setup_screen(
    draft: SetupDraft,
    group_locale: str | None = None,
    *,
    page: int = 0,
    notice_key: str | None = None,
    available_features: tuple[str, ...] = (),
) -> tuple[str, InlineKeyboardMarkup]:
    """Render the current guided setup step text and keyboard in the target group's preview locale."""
    locale = draft.draft.ui_locale or group_locale or DEFAULT_LOCALE
    summary = _format_summary_values(draft.draft, locale)
    builder = InlineKeyboardBuilder()
    step = draft.step

    if step == SetupStep.UI_LOCALE:
        text = (
            f"<b>{get_message('setup-step-ui-title', locale)}</b>\n\n"
            f"{get_message('setup-step-ui-body', locale, ui_locale=summary['ui_locale'])}"
        )
        for code in SUPPORTED_LOCALES:
            mark = "✅ " if (draft.draft.ui_locale or group_locale or DEFAULT_LOCALE) == code else ""
            builder.button(
                text=f"{mark}{_UI_LABELS.get(code, code)}",
                callback_data=SetupCallback(
                    action=SetupAction.SELECT_UI, session_id=draft.session_id, value=code
                ).pack(),
            )
        builder.adjust(2)
    elif step == SetupStep.TARGET_LANG:
        text = (
            f"<b>{get_message('setup-step-target-title', locale)}</b>\n\n"
            f"{get_message('setup-step-target-body', locale, target_lang=summary['target_lang'])}"
        )
        _append_language_page(builder, draft, page=page, ignored_mode=False)
    elif step == SetupStep.IGNORED_LANGS:
        text = (
            f"<b>{get_message('setup-step-ignored-title', locale)}</b>\n\n"
            f"{get_message('setup-step-ignored-body', locale, ignored=summary['ignored'])}"
        )
        _append_language_page(builder, draft, page=page, ignored_mode=True)
        builder.row(
            InlineKeyboardButton(
                text=get_message("setup-btn-clear-ignored", locale),
                callback_data=SetupCallback(action=SetupAction.CLEAR_IGNORED, session_id=draft.session_id).pack(),
            )
        )
    elif step == SetupStep.FEATURES:
        text = (
            f"<b>{get_message('setup-step-features-title', locale)}</b>\n\n"
            f"{get_message('setup-step-features-body', locale)}"
        )
        enabled = set(draft.draft.enabled_features)
        for key in available_features:
            mark = "✅ " if key in enabled else "☐ "
            builder.button(
                text=f"{mark}{_feature_display_label(key)}",
                callback_data=SetupCallback(
                    action=SetupAction.TOGGLE_FEATURE, session_id=draft.session_id, value=key
                ).pack(),
            )
        builder.adjust(2)
    elif step == SetupStep.SPEECH:
        text = (
            f"<b>{get_message('setup-step-speech-title', locale)}</b>\n\n"
            f"{get_message('setup-step-speech-body', locale, speech_mode=summary['speech_mode'])}"
        )
        current_mode = draft.draft.speech_mode or SpeechMode.OFF
        for mode in _SPEECH_MODES:
            mark = "✅ " if current_mode == mode else ""
            label = get_message(f"settings-speech-mode-{mode.value}", locale)
            builder.button(
                text=f"{mark}{label}",
                callback_data=SetupCallback(
                    action=SetupAction.SELECT_SPEECH, session_id=draft.session_id, value=mode.value
                ).pack(),
            )
        builder.adjust(1)
    else:
        text = (
            f"<b>{get_message('setup-step-review-title', locale)}</b>\n\n"
            f"{get_message('setup-step-review-body', locale, **summary)}"
        )

    if notice_key is not None:
        text = f"{get_message(notice_key, locale)}\n\n{text}"

    _build_nav_row(builder, draft.session_id, step, locale)
    return text, builder.as_markup()


def _append_language_page(
    builder: InlineKeyboardBuilder,
    draft: SetupDraft,
    *,
    page: int,
    ignored_mode: bool,
) -> None:
    codes = _ordered_language_codes()
    total_pages = max(1, (len(codes) + _LANGS_PER_PAGE - 1) // _LANGS_PER_PAGE)
    safe_page = max(0, min(page, total_pages - 1))
    slice_codes = codes[safe_page * _LANGS_PER_PAGE : (safe_page + 1) * _LANGS_PER_PAGE]
    selected_ignored = set(draft.draft.ignored_langs or ())
    action = SetupAction.TOGGLE_IGNORED if ignored_mode else SetupAction.SELECT_TARGET

    buttons: list[InlineKeyboardButton] = []
    for code in slice_codes:
        lang = LANGUAGES[code]
        is_selected = code in selected_ignored if ignored_mode else draft.draft.target_lang == code
        mark = "✅ " if is_selected else ""
        buttons.append(
            InlineKeyboardButton(
                text=f"{mark}{lang.display_name}",
                callback_data=SetupCallback(action=action, session_id=draft.session_id, value=code).pack(),
            )
        )
    for idx in range(0, len(buttons), 2):
        builder.row(*buttons[idx : idx + 2])

    if total_pages > 1:
        pager: list[InlineKeyboardButton] = []
        if safe_page > 0:
            pager.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=SetupCallback(
                        action=SetupAction.PAGE, session_id=draft.session_id, value=str(safe_page - 1)
                    ).pack(),
                )
            )
        if safe_page + 1 < total_pages:
            pager.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=SetupCallback(
                        action=SetupAction.PAGE, session_id=draft.session_id, value=str(safe_page + 1)
                    ).pack(),
                )
            )
        if pager:
            builder.row(*pager)


class SetupWizardHandler:
    """Coordinate group-bound sequential guided setup rendering and callback actions."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(
        requires_admin=False,
        requires_group_admin=False,
        requires_approval=True,
        is_toggleable=False,
    )

    def __init__(
        self,
        draft_service: SetupDraftService,
        apply_service: SetupApplyService,
        i18n_manager: AudienceI18nManager,
        settings: AppSettings,
        feature_access: FeatureAccessService,
    ) -> None:
        self._draft_service = draft_service
        self._apply_service = apply_service
        self._i18n_manager = i18n_manager
        self._settings = settings
        self._feature_access = feature_access

    async def _available_feature_keys(self, chat_id: int, actor_id: int) -> tuple[str, ...]:
        keys: list[str] = []
        for definition in self._feature_access.registry.all():
            if not definition.is_toggleable and definition.key != CommandName.TRANSCRIBE:
                continue
            if definition.key == CommandName.TRANSCRIBE and not self._settings.speech_enabled:
                continue
            decision = await self._feature_access.evaluate(definition.key, chat_id, actor_id, configure=True)
            if not decision.visible:
                continue
            if definition.key not in keys:
                keys.append(definition.key)
        return tuple(keys)

    def register(self, router: Router) -> None:
        """Register guided setup callbacks on the supplied router."""
        router.callback_query.register(
            self.handle_callback,
            SetupCallback.filter(),
            flags={"command_name": CommandName.SETUP, "policy": self.POLICY},
        )

    async def _actor_locale(self, actor_id: int) -> str:
        return await self._i18n_manager.get_user_locale(actor_id) or DEFAULT_LOCALE

    async def _group_locale(self, chat_id: int) -> str:
        return await self._i18n_manager.get_chat_locale(chat_id) or DEFAULT_LOCALE

    async def start_in_group(
        self,
        *,
        bot: Bot,
        chat_id: int,
        actor_id: int,
        thread_id: int | None,
    ) -> SetupSessionOutcome:
        """Start or resume guided setup directly inside a canonical approved group chat."""
        if chat_id >= 0:
            return SetupSessionOutcome.DENIED
        loaded = await self._apply_service.load_baseline(chat_id)
        if loaded is None:
            return SetupSessionOutcome.DENIED
        baseline, baseline_version = loaded
        started = await self._draft_service.start_or_resume(
            target_chat_id=chat_id,
            transport_chat_id=chat_id,
            actor_id=actor_id,
            message_id=None,
            thread_id=thread_id,
            baseline=baseline,
            baseline_version=baseline_version,
        )
        if started.outcome not in {SetupSessionOutcome.CREATED, SetupSessionOutcome.RESUMED} or started.draft is None:
            return started.outcome

        previous_message_id = started.draft.message_id if started.outcome == SetupSessionOutcome.RESUMED else 0
        group_locale = await self._group_locale(chat_id)
        available = (
            await self._available_feature_keys(chat_id, actor_id) if started.draft.step == SetupStep.FEATURES else ()
        )
        text, markup = render_setup_screen(started.draft, group_locale, available_features=available)
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        except TelegramAPIError:
            if started.outcome == SetupSessionOutcome.CREATED:
                await self._draft_service.release_session(chat_id=chat_id, session_id=started.draft.session_id)
            return SetupSessionOutcome.UNAVAILABLE

        bound = await self._draft_service.bind_message(
            session_id=started.draft.session_id,
            actor_id=actor_id,
            message_id=sent.message_id,
            thread_id=thread_id,
        )
        if bound.outcome != SetupSessionOutcome.UPDATED:
            return bound.outcome
        if (
            started.outcome == SetupSessionOutcome.RESUMED
            and previous_message_id > 0
            and previous_message_id != sent.message_id
        ):
            with contextlib.suppress(TelegramAPIError):
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=previous_message_id,
                    reply_markup=None,
                )
        return started.outcome

    async def handle_callback(self, query: CallbackQuery, callback_data: SetupCallback) -> None:
        """Handle guided setup callback interactions with strict owner, target, and authority checks."""
        if query.from_user is None:
            return
        actor_id = query.from_user.id
        actor_locale = await self._actor_locale(actor_id)
        msg = query.message
        if msg is None or isinstance(msg, InaccessibleMessage):
            await query.answer(get_message("setup-expired", actor_locale), show_alert=True)
            return

        chat_id = msg.chat.id
        thread_id = msg.message_thread_id
        if chat_id >= 0:
            await query.answer(get_message("setup-group-only", actor_locale), show_alert=True)
            return

        if callback_data.action == SetupAction.START:
            if msg.bot is None:
                return
            outcome = await self.start_in_group(
                bot=msg.bot,
                chat_id=chat_id,
                actor_id=actor_id,
                thread_id=thread_id,
            )
            await self._answer_start_outcome(query, outcome, actor_locale)
            return

        await self._handle_session_action(query, msg, callback_data, actor_id, actor_locale)

    async def _answer_start_outcome(
        self,
        query: CallbackQuery,
        outcome: SetupSessionOutcome,
        actor_locale: str,
    ) -> None:
        if outcome == SetupSessionOutcome.IN_PROGRESS:
            await query.answer(get_message("setup-in-progress", actor_locale), show_alert=True)
        elif outcome == SetupSessionOutcome.DENIED:
            await query.answer(get_message("setup-denied", actor_locale), show_alert=True)
        elif outcome == SetupSessionOutcome.UNAVAILABLE:
            await query.answer(get_message("setup-unavailable", actor_locale), show_alert=True)
        else:
            await query.answer()

    async def _handle_session_action(
        self,
        query: CallbackQuery,
        msg: Message,
        callback_data: SetupCallback,
        actor_id: int,
        actor_locale: str,
    ) -> None:
        action = callback_data.action
        if action in {SetupAction.CANCEL, SetupAction.APPLY, SetupAction.BACK}:
            await self._handle_navigation_action(query, msg, callback_data, actor_id, actor_locale)
            return

        loaded = await self._draft_service.load_owned(
            session_id=callback_data.session_id,
            transport_chat_id=msg.chat.id,
            actor_id=actor_id,
            message_id=msg.message_id,
            thread_id=msg.message_thread_id,
            callback_id=query.id,
        )
        if loaded.outcome != SetupSessionOutcome.UPDATED or loaded.draft is None:
            await self._answer_error(query, loaded.outcome, actor_locale)
            return

        if not self._is_action_valid_for_step(loaded.draft, callback_data):
            await query.answer(get_message("setup-duplicate", actor_locale))
            return

        if action == SetupAction.PAGE:
            page = int(callback_data.value) if callback_data.value.isdigit() else 0
            group_locale = await self._group_locale(msg.chat.id)
            text, markup = render_setup_screen(loaded.draft, group_locale, page=page)
            await self._safe_edit(msg, text, markup)
            await query.answer()
            return

        await self._apply_and_render_step(query, msg, loaded.draft, callback_data, actor_id, actor_locale)

    async def _handle_navigation_action(
        self,
        query: CallbackQuery,
        msg: Message,
        callback_data: SetupCallback,
        actor_id: int,
        actor_locale: str,
    ) -> None:
        action = callback_data.action
        session_id = callback_data.session_id
        if action == SetupAction.CANCEL:
            res = await self._draft_service.cancel(
                session_id=session_id,
                transport_chat_id=msg.chat.id,
                actor_id=actor_id,
                message_id=msg.message_id,
                thread_id=msg.message_thread_id,
                callback_id=query.id,
            )
            await self._finalize_cancel(query, msg, res.outcome, actor_locale)
            return
        if action == SetupAction.APPLY:
            await self._handle_apply(query, msg, session_id, actor_id, actor_locale)
            return
        res = await self._draft_service.go_back(
            session_id=session_id,
            transport_chat_id=msg.chat.id,
            actor_id=actor_id,
            message_id=msg.message_id,
            thread_id=msg.message_thread_id,
            callback_id=query.id,
        )
        await self._render_mutation_result(query, msg, res.outcome, res.draft, actor_locale, actor_id=actor_id)

    async def _apply_and_render_step(
        self,
        query: CallbackQuery,
        msg: Message,
        draft: SetupDraft,
        callback_data: SetupCallback,
        actor_id: int,
        actor_locale: str,
    ) -> None:
        available_features = (
            await self._available_feature_keys(msg.chat.id, actor_id) if draft.step == SetupStep.FEATURES else ()
        )
        applied_choice = self._apply_draft_choice(draft.draft, callback_data, available_features)
        if applied_choice is None:
            await query.answer(get_message("setup-unavailable", actor_locale), show_alert=True)
            return
        updated_values, advance, page = applied_choice
        res = await self._draft_service.update_and_advance(
            session_id=callback_data.session_id,
            transport_chat_id=msg.chat.id,
            actor_id=actor_id,
            message_id=msg.message_id,
            thread_id=msg.message_thread_id,
            callback_id=query.id,
            draft_values=updated_values,
            advance=advance,
        )
        await self._render_mutation_result(
            query, msg, res.outcome, res.draft, actor_locale, actor_id=actor_id, page=page
        )

    def _is_action_valid_for_step(self, draft: SetupDraft, callback_data: SetupCallback) -> bool:
        action = callback_data.action
        step = draft.step
        step_requirements = {
            SetupAction.SELECT_UI: SetupStep.UI_LOCALE,
            SetupAction.SELECT_TARGET: SetupStep.TARGET_LANG,
            SetupAction.TOGGLE_IGNORED: SetupStep.IGNORED_LANGS,
            SetupAction.CLEAR_IGNORED: SetupStep.IGNORED_LANGS,
            SetupAction.TOGGLE_FEATURE: SetupStep.FEATURES,
            SetupAction.SELECT_SPEECH: SetupStep.SPEECH,
        }
        if action in step_requirements:
            if action == SetupAction.SELECT_SPEECH and not self._settings.speech_enabled:
                return False
            return step == step_requirements[action]
        if action == SetupAction.PAGE:
            return step in {SetupStep.TARGET_LANG, SetupStep.IGNORED_LANGS}
        if action == SetupAction.NEXT:
            if step == SetupStep.REVIEW:
                return False
            return not callback_data.value or callback_data.value == step.value
        return True

    def _apply_draft_choice(
        self,
        current: SetupConfigurationValues,
        callback_data: SetupCallback,
        available_features: tuple[str, ...],
    ) -> tuple[SetupConfigurationValues, bool, int] | None:
        action = callback_data.action
        val = callback_data.value
        result: tuple[SetupConfigurationValues, bool, int] | None = (current, True, 0)
        if action == SetupAction.SELECT_UI:
            result = (current.model_copy(update={"ui_locale": val}), True, 0) if val in SUPPORTED_LOCALES else None
        elif action == SetupAction.SELECT_TARGET:
            canonical = resolve_language_code(val)
            result = (current.model_copy(update={"target_lang": canonical}), True, 0) if canonical is not None else None
        elif action == SetupAction.CLEAR_IGNORED:
            result = (current.model_copy(update={"ignored_langs": ()}), False, 0)
        elif action == SetupAction.TOGGLE_IGNORED:
            result = self._toggle_ignored_choice(current, val)
        elif action == SetupAction.TOGGLE_FEATURE:
            result = self._toggle_feature_choice(current, val, available_features)
        elif action == SetupAction.SELECT_SPEECH:
            result = self._select_speech_choice(current, val)
        return result

    def _toggle_ignored_choice(
        self,
        current: SetupConfigurationValues,
        val: str,
    ) -> tuple[SetupConfigurationValues, bool, int] | None:
        canonical = resolve_language_code(val)
        if canonical is None:
            return None
        existing = [c for c in (current.ignored_langs or ()) if c != "und"]
        if canonical in existing:
            existing.remove(canonical)
        else:
            existing.append(canonical)
        if existing:
            existing.append("und")
        codes = _ordered_language_codes()
        page = codes.index(canonical) // _LANGS_PER_PAGE if canonical in codes else 0
        return (current.model_copy(update={"ignored_langs": tuple(existing)}), False, page)

    def _toggle_feature_choice(
        self,
        current: SetupConfigurationValues,
        val: str,
        available_features: tuple[str, ...],
    ) -> tuple[SetupConfigurationValues, bool, int] | None:
        if val not in available_features:
            return None
        feats = list(current.enabled_features)
        if val in feats:
            feats.remove(val)
        else:
            feats.append(val)
        return (current.model_copy(update={"enabled_features": tuple(feats)}), False, 0)

    def _select_speech_choice(
        self,
        current: SetupConfigurationValues,
        val: str,
    ) -> tuple[SetupConfigurationValues, bool, int] | None:
        try:
            mode = SpeechMode(val)
        except ValueError:
            return None
        if mode == SpeechMode.OFF:
            feats = tuple(feat for feat in current.enabled_features if feat not in _SPEECH_FEATURE_KEYS)
            return (current.model_copy(update={"speech_mode": mode, "enabled_features": feats}), True, 0)
        return (current.model_copy(update={"speech_mode": mode}), True, 0)

    async def _handle_apply(
        self,
        query: CallbackQuery,
        msg: Message,
        session_id: str,
        actor_id: int,
        actor_locale: str,
    ) -> None:
        loaded = await self._draft_service.load_owned(
            session_id=session_id,
            transport_chat_id=msg.chat.id,
            actor_id=actor_id,
            message_id=msg.message_id,
            thread_id=msg.message_thread_id,
            callback_id=query.id,
        )
        if loaded.outcome != SetupSessionOutcome.UPDATED or loaded.draft is None:
            await self._answer_error(query, loaded.outcome, actor_locale)
            return
        if loaded.draft.step != SetupStep.REVIEW:
            await query.answer(get_message("setup-duplicate", actor_locale))
            return

        result = await self._apply_service.commit_draft(loaded.draft, actor_id=actor_id)
        if result.outcome == SetupApplyOutcome.APPLIED and result.applied_values is not None:
            group_locale = result.applied_values.ui_locale or await self._group_locale(msg.chat.id)
            summary = _format_summary_values(result.applied_values, group_locale)
            await self._safe_edit(msg, get_message("setup-applied-summary", group_locale, **summary), None)
            await query.answer()
            return
        if result.outcome == SetupApplyOutcome.CONFLICT:
            refreshed = await self._draft_service.load_owned(
                session_id=session_id,
                transport_chat_id=msg.chat.id,
                actor_id=actor_id,
                message_id=msg.message_id,
                thread_id=msg.message_thread_id,
            )
            if refreshed.draft is not None:
                group_locale = await self._group_locale(msg.chat.id)
                text, markup = render_setup_screen(refreshed.draft, group_locale, notice_key="setup-conflict")
                await self._safe_edit(msg, text, markup)
            await query.answer(get_message("setup-conflict", actor_locale), show_alert=True)
            return
        if result.outcome == SetupApplyOutcome.UNAVAILABLE:
            await query.answer(get_message("setup-unavailable", actor_locale), show_alert=True)
            return
        await query.answer(get_message("setup-denied", actor_locale), show_alert=True)

    async def _finalize_cancel(
        self,
        query: CallbackQuery,
        msg: Message,
        outcome: SetupSessionOutcome,
        actor_locale: str,
    ) -> None:
        if outcome != SetupSessionOutcome.CANCELLED:
            await self._answer_error(query, outcome, actor_locale)
            return
        group_locale = await self._group_locale(msg.chat.id)
        await self._safe_edit(msg, get_message("setup-cancelled", group_locale), None)
        await query.answer()

    async def _render_mutation_result(
        self,
        query: CallbackQuery,
        msg: Message,
        outcome: SetupSessionOutcome,
        draft: SetupDraft | None,
        actor_locale: str,
        *,
        actor_id: int,
        page: int = 0,
    ) -> None:
        if outcome != SetupSessionOutcome.UPDATED or draft is None:
            await self._answer_error(query, outcome, actor_locale)
            return
        group_locale = await self._group_locale(msg.chat.id)
        available = (
            await self._available_feature_keys(msg.chat.id, actor_id) if draft.step == SetupStep.FEATURES else ()
        )
        text, markup = render_setup_screen(draft, group_locale, page=page, available_features=available)
        await self._safe_edit(msg, text, markup)
        await query.answer()

    async def _answer_error(self, query: CallbackQuery, outcome: SetupSessionOutcome, actor_locale: str) -> None:
        key_map = {
            SetupSessionOutcome.EXPIRED: "setup-expired",
            SetupSessionOutcome.NOT_FOUND: "setup-expired",
            SetupSessionOutcome.IN_PROGRESS: "setup-in-progress",
            SetupSessionOutcome.DUPLICATE_CALLBACK: "setup-duplicate",
            SetupSessionOutcome.UNAVAILABLE: "setup-unavailable",
        }
        key = key_map.get(outcome, "setup-denied")
        await query.answer(get_message(key, actor_locale), show_alert=outcome != SetupSessionOutcome.DUPLICATE_CALLBACK)

    async def _safe_edit(self, msg: Message, text: str, reply_markup: InlineKeyboardMarkup | None) -> None:
        try:
            await msg.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        except TelegramBadRequest:
            return


class SetupCommand(BaseCommand):
    """Launch or resume guided sequential configuration inside an approved group chat."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(
        requires_admin=False,
        requires_group_admin=True,
        requires_approval=True,
        is_toggleable=False,
    )

    def __init__(self, wizard: SetupWizardHandler, i18n_manager: AudienceI18nManager) -> None:
        self._wizard = wizard
        self._i18n_manager = i18n_manager

    @property
    def name(self) -> str:
        """Command name."""
        return CommandName.SETUP

    @property
    def trigger(self) -> Command:
        """Trigger filter for /setup."""
        return Command(CommandName.SETUP)

    async def is_triggered(self, message: Message) -> bool:
        """Return whether the message invokes /setup."""
        return self._is_slash_command_triggered(message, CommandName.SETUP)

    async def handle(self, message: Message, bot: Bot, **kwargs: object) -> None:
        """Start or resume guided setup in the target group chat."""
        if message.from_user is None or message.from_user.is_bot or message.sender_chat is not None:
            return
        actor_locale = await self._i18n_manager.get_user_locale(message.from_user.id) or DEFAULT_LOCALE
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await message.answer(get_message("setup-group-only", actor_locale))
            return
        outcome = await self._wizard.start_in_group(
            bot=bot,
            chat_id=message.chat.id,
            actor_id=message.from_user.id,
            thread_id=message.message_thread_id,
        )
        if outcome == SetupSessionOutcome.IN_PROGRESS:
            await message.answer(get_message("setup-in-progress", actor_locale))
        elif outcome == SetupSessionOutcome.DENIED:
            await message.answer(get_message("setup-denied", actor_locale))
        elif outcome == SetupSessionOutcome.UNAVAILABLE:
            await message.answer(get_message("setup-unavailable", actor_locale))
