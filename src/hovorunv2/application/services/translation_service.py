"""Application service for text translation."""

from typing import NamedTuple

import aiohttp

from hovorunv2.application.data.languages import ISO_CODE_LEN, LANGUAGES
from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.application.services.translation_providers import BaseTranslationService
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class TranslationResult(NamedTuple):
    """Result of a translation operation."""

    text: str
    target_lang: str
    flag: str


class TranslationService:
    """Service to handle text translation using configured providers."""

    MANDATORY_IGNORED_LANGS: tuple[str, ...] = ("und",)
    DEFAULT_FLAG: str = "🌐"

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
        # Normalize and check centralized data
        code = lang_code.lower().split("-")[0].split("_")[0]
        if code in LANGUAGES:
            return LANGUAGES[code].flag

        # Fallback for regional or missing codes
        country_code = code.upper()
        if len(country_code) != ISO_CODE_LEN:
            return self.DEFAULT_FLAG
        return "".join(chr(127397 + ord(c)) for c in country_code)

    async def translate_if_needed(
        self,
        text: str,
        chat_id: int,
        platform: str = "telegram",
        session: aiohttp.ClientSession | None = None,
    ) -> TranslationResult | None:
        """Translate text if it's not in ignored languages for the specific chat."""
        if not text or not text.strip():
            return None

        # Fetch chat settings from language service
        target_lang, ignored_langs = await self._language_service.get_chat_settings(chat_id, platform)

        # Enforce mandatory ignores and include target_lang
        all_ignored = set(ignored_langs)
        all_ignored.add(target_lang)
        for lang in self.MANDATORY_IGNORED_LANGS:
            all_ignored.add(lang)

        actual_session = session or self._session
        result = await self._provider.translate(actual_session, text, target_lang)

        if result and result.source_lang not in all_ignored:
            return TranslationResult(text=result.text, target_lang=target_lang, flag=self.get_flag(result.source_lang))

        return None
