"""Application service for text translation."""

import html
import json
from contextlib import AsyncExitStack
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
        chat_repository: "SQLAlchemyChatRepository",
    ) -> None:
        """Initialize service with repository."""
        self._chat_repository = chat_repository
        self._default_target_lang = settings.translation_target_lang
        self._default_ignored_langs = settings.translation_ignored_langs

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

        # Fetch chat settings
        target_lang = self._default_target_lang
        ignored_langs = list(self._default_ignored_langs)

        chat = await self._chat_repository.get_by_id(chat_id, platform)
        if chat:
            if chat.target_lang:
                target_lang = chat.target_lang
            if chat.ignored_langs:
                # Safely handle both native JSON columns (lists) and text columns (strings)
                if isinstance(chat.ignored_langs, list):
                    ignored_langs = chat.ignored_langs
                elif isinstance(chat.ignored_langs, str):
                    try:
                        chat_ignored = json.loads(chat.ignored_langs)
                        if isinstance(chat_ignored, list):
                            ignored_langs = chat_ignored
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Failed to parse ignored_langs for chat %d", chat_id)

        # Enforce mandatory ignores and include target_lang
        all_ignored = set(ignored_langs)
        all_ignored.add(target_lang)
        for lang in self.MANDATORY_IGNORED_LANGS:
            all_ignored.add(lang)

        async with AsyncExitStack() as stack:
            actual_session = session
            if not actual_session:
                actual_session = await stack.enter_async_context(aiohttp.ClientSession())

            translated_text = await self._perform_translation(
                actual_session, text, target_lang, list(all_ignored)
            )

        if translated_text:
            return TranslationResult(
                text=translated_text,
                target_lang=target_lang,
                flag=self.get_flag(target_lang)
            )

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
                    if not isinstance(data, list) or len(data) < 3:
                        return None

                    src_lang = data[2] or "und"

                    if src_lang not in ignored_langs and data[0] and isinstance(data[0], list):
                        return "".join([
                            sentence[0] for sentence in data[0]
                            if isinstance(sentence, list) and sentence[0]
                        ])
        except Exception:
            logger.exception("Translation API call failed")

        return None