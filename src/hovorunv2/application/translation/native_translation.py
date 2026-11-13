"""Select optional local caption translation using fresh persisted preferences."""

import html
from typing import Protocol

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.translation.ports import (
    CancellationToken,
    LanguageDetectorPort,
    TranslationBudget,
    TranslationDocument,
    TranslationEnginePort,
    TranslationError,
    TranslationIntent,
)
from hovorunv2.application.translation.translation_rules import (
    MIN_DETECTABLE_CHARS,
    TranslationResult,
    resolve_target_language,
    sanitize_for_detection,
    should_translate_for_intent,
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
        document = TranslationDocument(text=text, chat_id=chat_id, intent=TranslationIntent.AUTOMATIC)
        return self.translate_document(document)

    def translate_document(
        self,
        document: TranslationDocument,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> TranslationResult | None:
        """Translate a whole document according to request intent and eligibility rules.

        Args:
            document: Whole-document translation request and scope metadata.
            cancel_token: Optional cooperative cancellation token.

        Returns:
            Complete translated result, or ``None`` when translation is skipped.

        Raises:
            ValueError: If an explicit request has invalid source text or target language.
        """
        if not self._can_proceed(document.budget, cancel_token, document.intent):
            return None
        raw_text = html.unescape(document.text)
        if document.intent == TranslationIntent.EXPLICIT and not raw_text.strip():
            msg = "Translation source text must not be empty"
            raise ValueError(msg)
        clean = sanitize_for_detection(raw_text)
        if sum(character.isalpha() for character in clean) < MIN_DETECTABLE_CHARS:
            return None
        return self._run_translation(document, raw_text, clean)

    def _can_proceed(
        self,
        budget: TranslationBudget | None,
        cancel_token: CancellationToken | None,
        intent: TranslationIntent,
    ) -> bool:
        if cancel_token is not None and cancel_token.is_cancelled():
            if intent == TranslationIntent.EXPLICIT:
                msg = "Translation request was cancelled"
                raise TranslationError(msg)
            return False
        if budget is not None and budget.is_expired():
            if intent == TranslationIntent.EXPLICIT:
                msg = "Translation deadline exceeded"
                raise TranslationError(msg)
            return False
        return True

    def _run_translation(
        self,
        document: TranslationDocument,
        raw_text: str,
        clean: str,
    ) -> TranslationResult | None:
        chat_target, ignored = self._current_languages(document.chat_id)
        try:
            target = resolve_target_language(
                document.target_lang,
                chat_target,
                self._settings.translation_target_lang,
            )
            source = normalize_language_code(self._detector.detect(clean))
            if not should_translate_for_intent(source, target, ignored, document.intent):
                return None
            translated = self._engine.translate(raw_text, source, target)
        except TranslationError, ValueError:
            if document.intent == TranslationIntent.EXPLICIT:
                raise
            return None
        stripped = translated.strip()
        if not stripped:
            if document.intent == TranslationIntent.EXPLICIT:
                msg = "Translation engine returned empty text"
                raise TranslationError(msg)
            return None
        return TranslationResult(stripped, target, translation_flag(source))

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
