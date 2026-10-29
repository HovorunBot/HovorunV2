"""Application service orchestrating multi-tier rich media extraction and formatting."""

import html
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import ClassVar

from hovorunv2.application.clients.base import BaseMediaClient
from hovorunv2.application.clients.config import ProviderConfig
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.features import TriggerKind
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.application.media.finalization import (
    QUOTED_MEDIA_NOTE,
    append_translation,
    escape_payload_authors,
    merge_quoted_media,
    prepare_caption_text,
)
from hovorunv2.application.services.feature_access_service import FeatureAccessService
from hovorunv2.application.services.translation_service import TranslationService

logger = logging.getLogger(__name__)


class MediaPipeline:
    """Orchestrates 4-tier media extraction, capture permissions, and payload finalization."""

    QUOTED_MEDIA_NOTE: ClassVar[str] = QUOTED_MEDIA_NOTE

    def __init__(
        self,
        extractor: MediaExtractor,
        translation_service: TranslationService,
        feature_access: FeatureAccessService,
    ) -> None:
        """Initialize pipeline with shared extractor, translation, and permission services."""
        self._extractor = extractor
        self._translation_service = translation_service
        self._features = feature_access

    async def process(
        self,
        client: BaseMediaClient,
        url: str,
        chat_id: int,
        *,
        actor_id: int | None,
        trigger: TriggerKind,
    ) -> RichMediaPayload | None:
        """Execute 4-tier extraction, verify capture permissions, and finalize payload."""
        definitions = tuple(self._features.registry.capture(client.name, is_video=video) for video in (True, False))
        admitted = False
        for definition in definitions:
            decision = await self._features.evaluate(definition.key, chat_id, actor_id, trigger=trigger)
            admitted = admitted or decision.allowed
        if not admitted:
            return None
        cleaned_url = url.rstrip(".,")

        raw_payload = await self._extract_raw(client, cleaned_url)
        feature = self._features.registry.capture(client.name, is_video=raw_payload.is_video)
        decision = await self._features.evaluate(feature.key, chat_id, actor_id, trigger=trigger)
        if not decision.allowed:
            logger.info(
                "Skipping %s in chat %d (capture mode disabled for is_video=%s)",
                client.name,
                chat_id,
                raw_payload.is_video,
            )
            return None

        return await self._finalize_payload(raw_payload, client.config, cleaned_url, chat_id)

    async def _extract_raw(self, client: BaseMediaClient, url: str) -> RichMediaPayload:
        """Run ordered strategies; only semantic backend failure permits recovery."""
        strategies: list[Callable[[], Awaitable[RichMediaPayload | None]]] = [partial(client.extract, url)]
        is_post_url = bool(client.config.post_url_pattern and client.config.post_url_pattern.search(url))
        if not is_post_url:
            strategies.append(partial(self._extract_generic, client, url))
        strategies.extend((partial(client.extract_fallback, url), partial(client.extract_metadata, url)))
        for strategy in strategies:
            try:
                payload = await strategy()
            except MediaExtractionError:
                logger.info("Extraction strategy failed for provider %s", client.name)
                continue
            if payload is not None:
                return payload
        msg = "Media extraction strategies are exhausted."
        raise MediaExtractionError(msg)

    async def _extract_generic(self, client: BaseMediaClient, url: str) -> RichMediaPayload:
        """Resolve provider credentials only when the generic backend strategy is attempted."""
        return await self._extractor.extract(url, cookies=client.get_ytdlp_cookies())

    async def _finalize_payload(
        self,
        payload: RichMediaPayload,
        config: ProviderConfig,
        original_url: str,
        chat_id: int,
    ) -> RichMediaPayload:
        """Escape HTML, translate content if needed, and merge quotes."""
        escape_payload_authors(payload, original_url)
        payload.content = await self._translate_text(payload.content, chat_id, strip_hashtags=config.strip_hashtags)

        if quote := payload.quoted_payload:
            if config.translate_quoted:
                quote.content = await self._translate_text(quote.content, chat_id)
            else:
                quote.content = html.escape(quote.content)

        merge_quoted_media(payload, config)

        return payload

    async def _translate_text(
        self,
        raw_text: str,
        chat_id: int,
        *,
        strip_hashtags: bool = False,
    ) -> str:
        """Clean, HTML-escape, and translate text if needed."""
        content = prepare_caption_text(raw_text, strip_hashtags=strip_hashtags)
        translated = await self._translation_service.translate_if_needed(content, chat_id)
        return append_translation(content, translated)
