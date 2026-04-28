"""Application service for text translation."""

import html
from http import HTTPStatus
from typing import NamedTuple

import aiohttp
import flag

from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class TranslationResult(NamedTuple):
    """Result of a translation operation."""

    text: str
    target_lang: str
    flag: str


class TranslationService:
    """Service to handle text translation using Google Translate API."""

    MANDATORY_IGNORED_LANGS: tuple[str, ...] = ("und",)
    DEFAULT_FLAG: str = "🌐"

    # Google Translate Response Index Mapping
    MIN_RESPONSE_PARTS: int = 3
    SRC_LANG_INDEX: int = 2
    SENTENCES_INDEX: int = 0
    TEXT_INDEX: int = 0

    def __init__(
        self,
        language_service: LanguageService,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize service with language service and session."""
        self._language_service = language_service
        self._session = session

    def get_flag(self, lang_code: str) -> str:
        """Get flag emoji for a language code."""
        # Strip regional suffixes (e.g., zh-CN -> zh) to prevent flag generator errors
        code = lang_code.lower().split("-")[0].split("_")[0]

        # Handle common language-to-country mapping mismatches
        lang_to_country = {
            "en": "US",
            "uk": "UA",
            "ja": "JP",
            "zh": "CN",
            "ko": "KR",
            "el": "GR",
            "hi": "IN",
            "he": "IL",
            "ar": "SA",
            "vi": "VN",
        }
        country_code = lang_to_country.get(code, code).upper()
        try:
            return flag.flag(country_code)
        except Exception:
            return self.DEFAULT_FLAG

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
        translated_text = await self._perform_translation(actual_session, text, target_lang, list(all_ignored))

        if translated_text:
            return TranslationResult(text=translated_text, target_lang=target_lang, flag=self.get_flag(target_lang))

        return None

    async def _perform_translation(
        self,
        session: aiohttp.ClientSession,
        text: str,
        target_lang: str,
        ignored_langs: list[str],
    ) -> str | None:
        """Perform the actual translation call."""
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": target_lang,
            "dt": "t",
            "q": html.unescape(text),
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == HTTPStatus.OK:
                    # content_type=None avoids strict application/json checks
                    data = await resp.json(content_type=None)

                    # Defensively check the response payload structure
                    if not isinstance(data, list) or len(data) < self.MIN_RESPONSE_PARTS:
                        return None

                    src_lang = data[self.SRC_LANG_INDEX] or "und"

                    if (
                        src_lang not in ignored_langs
                        and data[self.SENTENCES_INDEX]
                        and isinstance(data[self.SENTENCES_INDEX], list)
                    ):
                        return "".join(
                            [
                                sentence[self.TEXT_INDEX]
                                for sentence in data[self.SENTENCES_INDEX]
                                if isinstance(sentence, list) and sentence[self.TEXT_INDEX]
                            ]
                        )
        except Exception:
            logger.exception("Translation API call failed")

        return None
