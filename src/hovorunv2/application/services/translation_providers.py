"""Translation service implementations."""

from abc import ABC, abstractmethod
from http import HTTPStatus
from typing import Final

import aiohttp

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPTransportError
from hovorunv2.application.translation.translation_rules import (
    GOOGLE_TRANSLATION_URL,
    TranslationRawResult,
    google_translation_params,
    parse_google_translation,
)

__all__ = ["BaseTranslationService", "GoogleTranslationService", "TranslationRawResult"]


class BaseTranslationService(ABC):
    """Abstract base class for translation services."""

    @abstractmethod
    async def translate(
        self,
        session: aiohttp.ClientSession,
        text: str,
        target_lang: str,
    ) -> TranslationRawResult:
        """Return required provider facts; unusable responses or I/O raise HTTPTransportError."""


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
    ) -> TranslationRawResult:
        """Require usable Google translation data without exposing private response diagnostics."""
        params = google_translation_params(text, target_lang)

        try:
            async with session.get(self.API_URL, params=params) as response:
                return await self._read_response(response)
        except HTTPTransportError:
            raise
        except aiohttp.ClientError, OSError:
            raise HTTPTransportError from None

    async def _read_response(self, response: aiohttp.ClientResponse) -> TranslationRawResult:
        if response.status != HTTPStatus.OK:
            raise HTTPTransportError(HTTPFailure.REJECTED)
        try:
            data = await response.json(content_type=None)
        except ValueError:
            raise HTTPTransportError(HTTPFailure.MALFORMED) from None
        return parse_google_translation(data)
