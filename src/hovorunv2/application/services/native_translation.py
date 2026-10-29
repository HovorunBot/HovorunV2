"""Native worker translation with fresh settings and shared pure provider rules."""

import html
from typing import Protocol

from hovorunv2.application.ports import AppSettings
from hovorunv2.application.services.translation_rules import (
    GOOGLE_TRANSLATION_URL,
    TranslationResult,
    google_translation_params,
    parse_google_translation,
    select_translation,
)
from hovorunv2.application.worker_io import HTTPTransportError, SyncHTTPPort


class TranslationSettingsPort(Protocol):
    """Read current translation preferences without sharing an async bot store."""

    def chat_translation_settings(self, chat_id: int) -> tuple[str | None, tuple[str, ...] | None] | None:
        """Return current settings or absence, retaining the distinction between empty and unset ignores."""
        ...


class NativeTranslationService:
    """Translate synchronously with the same parsing, defaults and ignored-source rules as the bot."""

    def __init__(
        self,
        http: SyncHTTPPort,
        settings: AppSettings,
        preferences: TranslationSettingsPort,
        *,
        api_url: str = GOOGLE_TRANSLATION_URL,
    ) -> None:
        self._http = http
        self._settings = settings
        self._preferences = preferences
        self._api_url = api_url

    def translate_if_needed(self, text: str, chat_id: int) -> TranslationResult | None:
        """Read preferences for each operation and keep provider failures independent of media delivery."""
        if not text.strip():
            return None
        preferences = self._preferences.chat_translation_settings(chat_id)
        target = preferences[0] if preferences is not None else None
        ignored = preferences[1] if preferences is not None else None
        target = target or self._settings.translation_target_lang
        ignored = tuple(self._settings.translation_ignored_langs) if ignored is None else ignored
        try:
            raw = self._http.get_json(self._api_url, params=google_translation_params(text, target))
        except HTTPTransportError:
            return None
        return select_translation(parse_google_translation(raw), target, ignored)

    def escape_and_translate(self, raw_text: str, chat_id: int) -> str:
        """Escape raw captions and append the shared translation display block."""
        content = html.escape(raw_text)
        translated = self.translate_if_needed(content, chat_id)
        if translated is not None:
            content += f"\n\n{translated.flag} <b>Translated:</b>\n{html.escape(translated.text)}"
        return content
