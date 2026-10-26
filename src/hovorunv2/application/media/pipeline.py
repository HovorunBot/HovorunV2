"""Application service orchestrating multi-tier rich media extraction and formatting."""

import html
import logging
from typing import ClassVar

from hovorunv2.application.clients.base import BaseMediaClient, ProviderConfig
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.text import clean_caption_text

logger = logging.getLogger(__name__)


class MediaPipeline:
    """Orchestrates 4-tier media extraction, capture permissions, and payload finalization."""

    QUOTED_MEDIA_NOTE: ClassVar[str] = "\n\nℹ️️ <i>Post includes quoted media</i>"  # noqa: RUF001

    def __init__(
        self,
        extractor: MediaExtractor,
        translation_service: TranslationService,
        command_service: CommandService,
    ) -> None:
        """Initialize pipeline with shared extractor, translation, and permission services."""
        self._extractor = extractor
        self._translation_service = translation_service
        self._command_service = command_service

    async def process(
        self,
        client: BaseMediaClient,
        url: str,
        chat_id: int,
    ) -> RichMediaPayload | None:
        """Execute 4-tier extraction, verify capture permissions, and finalize payload."""
        cleaned_url = url.rstrip(".,")

        raw_payload = await self._extract_raw(client, cleaned_url)
        if not raw_payload:
            logger.warning("Extraction returned empty payload for provider %s", client.name)
            return None

        if not await self._command_service.is_capture_allowed(chat_id, client.name, is_video=raw_payload.is_video):
            logger.info(
                "Skipping %s in chat %d (capture mode disabled for is_video=%s)",
                client.name,
                chat_id,
                raw_payload.is_video,
            )
            return None

        return await self._finalize_payload(raw_payload, client.config, cleaned_url, chat_id)

    async def _extract_raw(self, client: BaseMediaClient, url: str) -> RichMediaPayload | None:
        """Run 4-tier resilience pipeline: Direct API -> yt-dlp -> Browser Fallback -> Metadata."""
        # 1. Direct API
        if payload := await client.extract(url):
            return payload

        # 2. Generic MediaExtractor (yt-dlp) - skip for explicit post/text URLs
        is_post_url = bool(client.config.post_url_pattern and client.config.post_url_pattern.search(url))
        if not is_post_url:
            logger.info("%s direct API returned no payload, trying yt-dlp", client.name)
            if payload := await self._extractor.extract(url, cookies=client.get_ytdlp_cookies()):
                return payload

        # 3. Browser Fallback
        if payload := await client.extract_fallback(url):
            return payload

        # 4. Metadata / OpenGraph fallback
        return await client.extract_metadata(url)

    async def _finalize_payload(
        self,
        payload: RichMediaPayload,
        config: ProviderConfig,
        original_url: str,
        chat_id: int,
    ) -> RichMediaPayload:
        """Escape HTML, translate content if needed, and merge quotes."""
        payload.author_name = html.escape(payload.author_name)
        payload.author_handle = html.escape(payload.author_handle)
        payload.original_url = original_url
        payload.content = await self._translate_text(payload.content, chat_id, strip_hashtags=config.strip_hashtags)

        if quote := payload.quoted_payload:
            quote.author_name = html.escape(quote.author_name)
            quote.author_handle = html.escape(quote.author_handle)
            if config.translate_quoted:
                quote.content = await self._translate_text(quote.content, chat_id)
            else:
                quote.content = html.escape(quote.content)

            if (
                quote.media_items
                and config.merge_quoted_media
                and (not config.merge_quoted_only_when_empty or not payload.media_items)
            ):
                payload.media_items.extend(quote.media_items)
                payload.content += self.QUOTED_MEDIA_NOTE

        return payload

    async def _translate_text(
        self,
        raw_text: str,
        chat_id: int,
        *,
        strip_hashtags: bool = False,
    ) -> str:
        """Clean, HTML-escape, and translate text if needed."""
        text_to_escape = clean_caption_text(raw_text, strip_hashtags=True) if strip_hashtags else raw_text
        content = html.escape(text_to_escape)
        trans_res = await self._translation_service.translate_if_needed(content, chat_id)
        if trans_res:
            content += f"\n\n{trans_res.flag} <b>Translated:</b>\n{html.escape(trans_res.text)}"
        return content
