"""Application service for text translation."""

import html
import logging

import aiohttp

from hovorunv2.application.jobs.http_ports import HTTPTransportError
from hovorunv2.application.translation.language_service import LanguageService
from hovorunv2.application.translation.providers import BaseTranslationService
from hovorunv2.application.translation.translation_rules import (
    DEFAULT_TRANSLATION_FLAG,
    MANDATORY_IGNORED_LANGS,
    TranslationResult,
    select_translation,
    translation_flag,
)

__all__ = ["TranslationResult", "TranslationService"]

logger = logging.getLogger(__name__)


class TranslationService:
    """Service to handle text translation using configured providers."""

    MANDATORY_IGNORED_LANGS: tuple[str, ...] = MANDATORY_IGNORED_LANGS
    DEFAULT_FLAG: str = DEFAULT_TRANSLATION_FLAG

    def __init__(
        self,
        language_service: LanguageService,
        translation_provider: BaseTranslationService,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize service with dependencies."""
        self._language_service = language_service
        self._provider = translation_provider
        self._session = session

    def get_flag(self, lang_code: str) -> str:
        """Get flag emoji for a language code."""
        return translation_flag(lang_code)

    async def translate_if_needed(
        self,
        text: str,
        chat_id: int,
        session: aiohttp.ClientSession | None = None,
    ) -> TranslationResult | None:
        """Translate text if it's not in ignored languages for the specific chat."""
        if not text.strip():
            return None

        # Fetch chat settings from language service
        target_lang, ignored_langs = await self._language_service.get_chat_settings(chat_id)

        actual_session = session or self._session
        try:
            result = await self._provider.translate(actual_session, text, target_lang)
        except HTTPTransportError:
            logger.info("Translation provider is unavailable")
            return None
        return select_translation(result, target_lang, ignored_langs)

    async def escape_and_translate(
        self,
        raw_text: str,
        chat_id: int,
        session: aiohttp.ClientSession | None = None,
    ) -> str:
        """HTML-escape raw text and append translation block if needed."""
        content = html.escape(raw_text)
        if trans_res := await self.translate_if_needed(content, chat_id, session):
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"
        return content
