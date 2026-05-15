"""Translation service implementations."""

import html
from abc import ABC, abstractmethod
from http import HTTPStatus
from typing import Final, NamedTuple

import aiohttp

from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class TranslationRawResult(NamedTuple):
    """Raw result from a translation provider."""

    text: str
    source_lang: str


class BaseTranslationService(ABC):
    """Abstract base class for translation services."""

    @abstractmethod
    async def translate(
        self,
        session: aiohttp.ClientSession,
        text: str,
        target_lang: str,
    ) -> TranslationRawResult | None:
        """Translate text to the target language."""


class GoogleTranslationService(BaseTranslationService):
    """Google Translate API implementation."""

    API_URL: Final = "https://translate.googleapis.com/translate_a/single"

    # Response index mapping
    SENTENCES_INDEX: Final = 0
    SRC_LANG_INDEX: Final = 2
    TEXT_INDEX: Final = 0

    async def translate(
        self,
        session: aiohttp.ClientSession,
        text: str,
        target_lang: str,
    ) -> TranslationRawResult | None:
        """Translate text using Google Translate API."""
        # Map some common aliases if needed
        tl = target_lang
        if tl == "zh":
            tl = "zh-CN"

        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": tl,
            "dt": "t",
            "q": html.unescape(text),
        }

        try:
            async with session.get(self.API_URL, params=params) as resp:
                if resp.status != HTTPStatus.OK:
                    logger.error("Google Translate returned status %d", resp.status)
                    return None

                data = await resp.json(content_type=None)
                if not isinstance(data, list) or len(data) <= self.SRC_LANG_INDEX:
                    return None

                src_lang = data[self.SRC_LANG_INDEX] or "und"

                # Normalize common aliases back to canonical form
                if src_lang == "iw":
                    src_lang = "he"
                if src_lang == "ji":
                    src_lang = "yi"

                if data[self.SENTENCES_INDEX] and isinstance(data[self.SENTENCES_INDEX], list):
                    translated_text = "".join(
                        [
                            sentence[self.TEXT_INDEX]
                            for sentence in data[self.SENTENCES_INDEX]
                            if isinstance(sentence, list) and sentence[self.TEXT_INDEX]
                        ]
                    )
                    return TranslationRawResult(text=translated_text, source_lang=src_lang)
        except Exception:
            logger.exception("Google Translation API call failed")

        return None
