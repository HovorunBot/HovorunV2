"""Translation service implementations."""

import html
import logging
from abc import ABC, abstractmethod
from http import HTTPStatus
from typing import Final, NamedTuple

import aiohttp

logger = logging.getLogger(__name__)


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

                sentences = data[self.SENTENCES_INDEX]
                if not isinstance(sentences, list) or not sentences:
                    return None

                translated_text = "".join(
                    sentence[self.TEXT_INDEX]
                    for sentence in sentences
                    if isinstance(sentence, list)
                    and len(sentence) > self.TEXT_INDEX
                    and isinstance(sentence[self.TEXT_INDEX], str)
                )
                return TranslationRawResult(text=translated_text, source_lang=src_lang)
        except Exception:
            logger.exception("Google Translation API call failed")

        return None
