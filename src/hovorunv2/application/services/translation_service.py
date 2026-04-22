"""Application service for text translation."""

import html
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, NamedTuple

import aiohttp
import flag

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from hovorunv2.infrastructure.database.repositories.chat_repository import SQLAlchemyChatRepository

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

    def __init__(
        self,
        chat_repository: SQLAlchemyChatRepository,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize service with optional session and repository."""
        self._session = session
        self._chat_repository = chat_repository
        self._default_target_lang = settings.translation_target_lang
        self._default_ignored_langs = settings.translation_ignored_langs

    def get_flag(self, lang_code: str) -> str:
        """Get flag emoji for a language code."""
        code = lang_code.lower()
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
        """Translate text if it's not in ignored languages for the specific chat.

        Args:
            text: Text to translate.
            chat_id: ID of the chat to fetch settings for.
            platform: Platform name.
            session: Optional aiohttp session.

        Returns:
            TranslationResult or None if translation is not needed or fails.
        """
        if not text or not text.strip():
            return None

        # Fetch chat settings
        target_lang = self._default_target_lang
        ignored_langs = list(self._default_ignored_langs)

        chat = await self._chat_repository.get_by_id(chat_id, platform)
        if chat:
            if chat.target_lang:
                target_lang = chat.target_lang
            if chat.ignored_langs:
                try:
                    chat_ignored = json.loads(chat.ignored_langs)
                    if isinstance(chat_ignored, list):
                        ignored_langs = chat_ignored
                except json.JSONDecodeError:
                    logger.warning("Failed to parse ignored_langs for chat %d", chat_id)

        # Enforce mandatory ignores
        for lang in self.MANDATORY_IGNORED_LANGS:
            if lang not in ignored_langs:
                ignored_langs.append(lang)

        # Use provided session or fallback to internal one
        actual_session = session or self._session
        translated_text = None
        if not actual_session:
            async with aiohttp.ClientSession() as new_session:
                translated_text = await self._perform_translation(new_session, text, target_lang, ignored_langs)
        else:
            translated_text = await self._perform_translation(actual_session, text, target_lang, ignored_langs)

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
                    data = await resp.json()
                    # Google Translate API structure: index 2 is source language
                    source_lang_index = 2
                    src_lang = data[source_lang_index]

                    if src_lang not in ignored_langs:
                        return "".join([sentence[0] for sentence in data[0] if sentence[0]])
        except Exception:
            logger.exception("Translation API call failed")

        return None
