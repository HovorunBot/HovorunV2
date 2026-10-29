"""Pure caption preparation and quote merging shared by bot and native extraction pipelines."""

import html

from hovorunv2.application.clients.config import ProviderConfig
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.services.translation_rules import TranslationResult
from hovorunv2.application.text import clean_caption_text

QUOTED_MEDIA_NOTE = "\n\nℹ️️ <i>Post includes quoted media</i>"  # noqa: RUF001


def prepare_caption_text(raw_text: str, *, strip_hashtags: bool = False) -> str:
    """Clean provider-specific hashtags and escape raw text exactly once before translation."""
    text = clean_caption_text(raw_text, strip_hashtags=True) if strip_hashtags else raw_text
    return html.escape(text)


def append_translation(content: str, translated: TranslationResult | None) -> str:
    """Append the common translation display block without allowing translated markup."""
    if translated is None:
        return content
    return f"{content}\n\n{translated.flag} <b>Translated:</b>\n{html.escape(translated.text)}"


def escape_payload_authors(payload: RichMediaPayload, original_url: str) -> None:
    """Prepare author display fields and preserve the accepted original source URL."""
    payload.author_name = html.escape(payload.author_name)
    payload.author_handle = html.escape(payload.author_handle)
    payload.original_url = original_url
    if quote := payload.quoted_payload:
        quote.author_name = html.escape(quote.author_name)
        quote.author_handle = html.escape(quote.author_handle)


def merge_quoted_media(payload: RichMediaPayload, config: ProviderConfig) -> None:
    """Apply provider-specific quote merge policy after text processing."""
    quote = payload.quoted_payload
    if quote is None or not quote.media_items:
        return
    if not config.merge_quoted_media:
        return
    if config.merge_quoted_only_when_empty and payload.media_items:
        return
    payload.media_items.extend(quote.media_items)
    payload.content += QUOTED_MEDIA_NOTE
