"""Translation service implementations."""

import logging
from abc import ABC, abstractmethod
from http import HTTPStatus
from typing import Final

import aiohttp

from hovorunv2.application.services.translation_rules import (
    GOOGLE_TRANSLATION_URL,
    TranslationRawResult,
    google_translation_params,
    parse_google_translation,
)

__all__ = ["BaseTranslationService", "GoogleTranslationService", "TranslationRawResult"]

logger = logging.getLogger(__name__)


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

    API_URL: Final = GOOGLE_TRANSLATION_URL

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
        params = google_translation_params(text, target_lang)

        try:
            async with session.get(self.API_URL, params=params) as resp:
                if resp.status != HTTPStatus.OK:
                    logger.error("Google Translate returned status %d", resp.status)
                    return None

                data = await resp.json(content_type=None)
                return parse_google_translation(data)
        except Exception:
            logger.exception("Google Translation API call failed")

        return None
