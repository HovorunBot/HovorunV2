"""Route whole-document translation online-first with atomic local fallback."""

import logging

from hovorunv2.application.translation.ports import TranslationEnginePort, TranslationError

logger = logging.getLogger(__name__)


class HybridTranslationEngine(TranslationEnginePort):
    """Prefer an online translation engine and fall back atomically to a local engine."""

    def __init__(
        self,
        online_engine: TranslationEnginePort,
        local_engine: TranslationEnginePort,
        *,
        online_enabled: bool = True,
    ) -> None:
        self._online_engine = online_engine
        self._local_engine = local_engine
        self._online_enabled = online_enabled

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate the complete document online first, restarting locally on failure.

        Args:
            text: Complete source text to translate.
            source_lang: Normalized source language code.
            target_lang: Normalized target language code.

        Returns:
            Complete translated text from a single backend.
        """
        if not self._online_enabled:
            return self._local_engine.translate(text, source_lang, target_lang)

        try:
            return self._online_engine.translate(text, source_lang, target_lang)
        except (TranslationError, ValueError) as exc:
            logger.warning(
                "Online translation failed (%s); falling back to local engine",
                type(exc).__name__,
            )
        return self._local_engine.translate(text, source_lang, target_lang)
