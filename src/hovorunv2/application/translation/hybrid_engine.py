"""Route whole-document translation online-first with atomic local fallback."""

import logging

from hovorunv2.application.translation.ports import (
    CancellationToken,
    TranslationBudget,
    TranslationEnginePort,
    TranslationError,
    check_translation_controls,
)

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

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        *,
        budget: TranslationBudget | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> str:
        """Translate the complete document online first, restarting locally on failure.

        Args:
            text: Complete source text to translate.
            source_lang: Normalized source language code.
            target_lang: Normalized target language code.
            budget: Original monotonic execution deadline, when provided.
            cancel_token: Live cooperative cancellation context, when provided.

        Returns:
            Complete translated text from a single backend.
        """
        check_translation_controls(budget, cancel_token)
        if not self._online_enabled:
            return self._local_engine.translate(
                text, source_lang, target_lang, budget=budget, cancel_token=cancel_token
            )

        try:
            return self._online_engine.translate(
                text, source_lang, target_lang, budget=budget, cancel_token=cancel_token
            )
        except (TranslationError, ValueError) as exc:
            logger.warning(
                "Online translation failed (%s); falling back to local engine",
                type(exc).__name__,
            )
        check_translation_controls(budget, cancel_token)
        return self._local_engine.translate(text, source_lang, target_lang, budget=budget, cancel_token=cancel_token)
