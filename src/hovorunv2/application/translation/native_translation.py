"""Select optional local caption translation using fresh persisted preferences."""

import html
from typing import Protocol

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.translation.ports import LanguageDetectorPort, TranslationEnginePort, TranslationError
from hovorunv2.application.translation.translation_rules import (
    MIN_DETECTABLE_CHARS,
    TranslationResult,
    sanitize_for_detection,
    should_translate_language,
    translation_flag,
)
from hovorunv2.domain.languages import normalize_language_code


class TranslationSettingsPort(Protocol):
    """Read current translation preferences without sharing an async bot store."""

    def chat_translation_settings(self, chat_id: int) -> tuple[str | None, tuple[str, ...] | None] | None:
        """Return current settings or absence, retaining the distinction between empty and unset ignores."""
        ...


class NativeTranslationService:
    """Gate local inference with text detection and current chat preferences."""

    def __init__(
        self,
        detector: LanguageDetectorPort,
        engine: TranslationEnginePort,
        settings: AppSettings,
        preferences: TranslationSettingsPort,
    ) -> None:
        self._detector = detector
        self._engine = engine
        self._settings = settings
        self._preferences = preferences

    def translate_if_needed(self, text: str, chat_id: int) -> TranslationResult | None:
        """Read preferences for each operation and keep provider failures independent of media delivery.

        Args:
            text: Text to translate.
            chat_id: Telegram ID of the chat whose preferences apply.

        Returns:
            Selected translation, or None for blank text, ignored languages, or provider failure.
        """
        raw_text = html.unescape(text)
        clean = sanitize_for_detection(raw_text)
        if sum(character.isalpha() for character in clean) < MIN_DETECTABLE_CHARS:
            return None
        target, ignored = self._current_languages(chat_id)
        try:
            source = normalize_language_code(self._detector.detect(clean))
            if not should_translate_language(source, target, ignored):
                return None
            translated = self._engine.translate(raw_text, source, normalize_language_code(target))
        except TranslationError, ValueError:
            return None
        if not translated.strip():
            return None
        return TranslationResult(translated.strip(), target, translation_flag(source))

    def _current_languages(self, chat_id: int) -> tuple[str, tuple[str, ...]]:
        preferences = self._preferences.chat_translation_settings(chat_id)
        target, ignored = preferences if preferences is not None else (None, None)
        target = target or self._settings.translation_target_lang
        ignored = tuple(self._settings.translation_ignored_langs) if ignored is None else ignored
        return target, ignored

    def escape_and_translate(self, raw_text: str, chat_id: int) -> str:
        """Escape raw captions and append the shared translation display block.

        Args:
            raw_text: Caption to escape and optionally translate.
            chat_id: Telegram ID of the chat whose preferences apply.

        Returns:
            Escaped caption with the translated block appended when applicable.
        """
        content = html.escape(raw_text)
        translated = self.translate_if_needed(content, chat_id)
        if translated is not None:
            content += f"\n\n{translated.flag} <b>Translated:</b>\n{html.escape(translated.text)}"
        return content
