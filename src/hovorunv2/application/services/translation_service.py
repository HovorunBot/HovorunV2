"""Application service for text translation."""

import html
from http import HTTPStatus

import aiohttp

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class TranslationService:
    """Service to handle text translation using Google Translate API."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize service with optional session."""
        self._session = session
        self._target_lang = settings.translation_target_lang
        self._ignored_langs = settings.translation_ignored_langs

    async def translate_if_needed(self, text: str, session: aiohttp.ClientSession | None = None) -> str | None:
        """Translate text if it's not in ignored languages.

        Args:
            text: Text to translate.
            session: Optional aiohttp session.

        Returns:
            Translated text or None if translation is not needed or fails.
        """
        if not text or not text.strip():
            return None

        # Use provided session or fallback to internal one
        actual_session = session or self._session
        if not actual_session:
            async with aiohttp.ClientSession() as new_session:
                return await self._perform_translation(new_session, text)

        return await self._perform_translation(actual_session, text)

    async def _perform_translation(self, session: aiohttp.ClientSession, text: str) -> str | None:
        """Perform the actual translation call."""
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": self._target_lang,
            "dt": "t",
            "q": html.unescape(text),
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == HTTPStatus.OK:
                    data = await resp.json()
                    src_lang = data[2]

                    if src_lang not in self._ignored_langs:
                        return "".join([sentence[0] for sentence in data[0] if sentence[0]])
        except Exception:
            logger.exception("Translation API call failed")

        return None
