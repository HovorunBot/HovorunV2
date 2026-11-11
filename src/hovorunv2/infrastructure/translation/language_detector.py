"""Detect caption languages locally without loading a neural translation engine."""

lazy import py3langid

from hovorunv2.application.translation.ports import LanguageDetectorPort, TranslationError
from hovorunv2.application.translation.translation_rules import MIN_DETECTABLE_CHARS, sanitize_for_detection
from hovorunv2.domain.languages import normalize_language_code


class Py3LangIdDetector(LanguageDetectorPort):
    """Classify caption language using the local py3langid statistical model."""

    def detect(self, text: str) -> str:
        """Return the normalized language identified from a caption.

        Args:
            text: Caption text to classify.

        Returns:
            Normalized detected source language or ``und`` for insufficient evidence.

        Raises:
            TranslationError: If the local classifier fails.
        """
        clean = sanitize_for_detection(text)
        if sum(character.isalpha() for character in clean) < MIN_DETECTABLE_CHARS:
            return "und"
        try:
            language, _confidence = py3langid.classify(clean)
        except Exception:
            msg = "Local language detection failed"
            raise TranslationError(msg) from None
        return normalize_language_code(language)
