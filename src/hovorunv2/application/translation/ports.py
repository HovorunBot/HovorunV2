"""Define local detection and whole-document translation contracts without backend dependencies."""

import math
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class TranslationIntent(StrEnum):
    """Distinguish automatic caption translation from explicit user requests."""

    AUTOMATIC = "automatic"
    EXPLICIT = "explicit"


class TranslationScope(StrEnum):
    """Identify whether translation covers the full source or an explicit quote."""

    FULL_SOURCE = "full_source"
    SELECTED_QUOTE = "selected_quote"


@dataclass(frozen=True, slots=True)
class TranslationBudget:
    """Monotonic deadline budget shared across retry and fallback stages."""

    deadline_monotonic: float

    @classmethod
    def from_timeout(cls, timeout_seconds: float, now_monotonic: float | None = None) -> TranslationBudget:
        """Create a budget from a finite positive timeout in seconds.

        Args:
            timeout_seconds: Maximum allowed cumulative duration in seconds.
            now_monotonic: Optional monotonic clock reading for deterministic tests.

        Returns:
            A budget anchored to ``now_monotonic + timeout_seconds``.

        Raises:
            ValueError: If ``timeout_seconds`` is not finite and positive.
        """
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            msg = "Translation timeout must be finite and positive"
            raise ValueError(msg)
        base = time.monotonic() if now_monotonic is None else now_monotonic
        return cls(deadline_monotonic=base + timeout_seconds)

    def remaining_seconds(self, now_monotonic: float | None = None) -> float:
        """Return non-negative seconds remaining before the deadline."""
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return max(0.0, self.deadline_monotonic - now)

    def is_expired(self, now_monotonic: float | None = None) -> bool:
        """Return whether the monotonic deadline has elapsed."""
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return now >= self.deadline_monotonic


class CancellationToken(Protocol):
    """Cooperative cancellation signal checked before and between translation stages."""

    def is_cancelled(self) -> bool:
        """Return whether the caller cancelled this translation request."""
        ...


@dataclass(frozen=True, slots=True, repr=False)
class TranslationDocument:
    """Whole-document translation request with redacted representation."""

    text: str
    chat_id: int
    intent: TranslationIntent = TranslationIntent.AUTOMATIC
    scope: TranslationScope = TranslationScope.FULL_SOURCE
    source_message_id: int | None = None
    target_lang: str | None = None
    budget: TranslationBudget | None = None

    def __repr__(self) -> str:
        """Return metadata without exposing source text contents."""
        return (
            "TranslationDocument("
            f"chat_id={self.chat_id!r}, "
            f"intent={self.intent.value!r}, "
            f"scope={self.scope.value!r}, "
            f"source_message_id={self.source_message_id!r}, "
            f"target_lang={self.target_lang!r}, "
            f"text_len={len(self.text)})"
        )


class TranslationError(Exception):
    """Report an unavailable or failed translation without including caption contents."""


def check_translation_controls(
    budget: TranslationBudget | None,
    cancel_token: CancellationToken | None,
) -> None:
    """Raise a safe failure if the caller cancelled work or its original allowance expired."""
    if cancel_token is not None and cancel_token.is_cancelled():
        msg = "Translation request was cancelled"
        raise TranslationError(msg)
    if budget is not None and budget.is_expired():
        msg = "Translation deadline exceeded"
        raise TranslationError(msg)


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

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        *,
        budget: TranslationBudget | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> str:
        """Return usable translation or raise a semantic failure for caller recovery.

        Args:
            text: Original caption with decoded HTML entities.
            source_lang: Normalized source language identifier.
            target_lang: Normalized target language identifier.
            budget: Original monotonic execution deadline, when provided.
            cancel_token: Live cooperative cancellation context, when provided.

        Returns:
            Nonblank translated text.

        Raises:
            ValueError: If input or the language pair is unsupported.
            TranslationError: If provisioning or inference fails to produce a translation.
        """
        ...


class TranslationCatalogPort(Protocol):
    """Render localized translation UI labels and status text without framework dependencies."""

    def render(self, key: str, locale: str | None = None, **kwargs: object) -> str:
        """Format a localized message key for the given locale.

        Args:
            key: Catalog message identifier.
            locale: Optional target UI locale code.
            **kwargs: Named interpolation parameters.

        Returns:
            Localized formatted string.
        """
        ...
