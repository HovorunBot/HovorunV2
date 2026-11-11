"""Define local detection and translation contracts without backend dependencies."""

from typing import Protocol


class TranslationError(Exception):
    """Report an unavailable or failed translation without including caption contents."""


class LanguageDetectorPort(Protocol):
    """Detect source language independently of translation inference."""

    def detect(self, text: str) -> str:
        """Return a normalized language identifier or ``und`` for insufficient evidence.

        Args:
            text: Sanitized caption text to classify.

        Returns:
            Detected language identifier or ``und``.
        """
        ...


class TranslationEnginePort(Protocol):
    """Translate captions without exposing transport or model ownership."""

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Return usable translation or raise a semantic failure for caller recovery.

        Args:
            text: Original caption with decoded HTML entities.
            source_lang: Normalized source language identifier.
            target_lang: Normalized target language identifier.

        Returns:
            Nonblank translated text.

        Raises:
            ValueError: If input or the language pair is unsupported.
            TranslationError: If provisioning or inference fails to produce a translation.
        """
        ...
