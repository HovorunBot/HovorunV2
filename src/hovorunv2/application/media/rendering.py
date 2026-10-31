"""Pure shared Telegram caption and quote rendering, independent of transports."""

import html
from dataclasses import dataclass

from hovorunv2.application.media.payloads import RichMediaPayload

CAPTION_LIMIT = 1024
HEADER_TEMPLATE = (
    '🗣 <b>{tg_user_name}</b> shared a post by <b>{author_name}</b> (<a href="{author_url}">@{author_handle}</a>)\n\n'
)
QUOTE_TEMPLATE = (
    '<blockquote>🔄 <b>{author_name}</b> (<a href="{author_url}">@{author_handle}</a>)\n{content}</blockquote>\n'
)
METRICS_TEMPLATE = "{metrics}\n"
FOOTER_TEMPLATE = '🔗 <a href="{url}">Open on {platform}</a>'


@dataclass(frozen=True, slots=True)
class CaptionPlan:
    """A bounded media caption and optional preceding text message."""

    caption: str
    separate_text: str | None = None


def _header(payload: RichMediaPayload, sender_label: str | None) -> str:
    return HEADER_TEMPLATE.format(
        tg_user_name=html.escape(sender_label or "Someone"),
        author_name=payload.author_name,
        author_handle=payload.author_handle,
        author_url=payload.author_url,
    )


def _footer(payload: RichMediaPayload, platform_name: str) -> str:
    return FOOTER_TEMPLATE.format(url=payload.original_url, platform=platform_name)


def build_caption(payload: RichMediaPayload, sender_label: str | None, platform_name: str) -> str:
    """Preserve prepared HTML content and quotes while escaping the transient sender label."""
    content = payload.content + "\n\n" if payload.content else ""
    quote = ""
    if payload.quoted_payload:
        quote = QUOTE_TEMPLATE.format(
            author_name=payload.quoted_payload.author_name,
            author_handle=payload.quoted_payload.author_handle,
            author_url=payload.quoted_payload.author_url,
            content=payload.quoted_payload.content,
        )
    metrics = ""
    if payload.footer_text and payload.footer_text.strip():
        metrics = METRICS_TEMPLATE.format(metrics=payload.footer_text.strip().replace("\n", " "))
    return f"{_header(payload, sender_label)}{content}{quote}{metrics}{_footer(payload, platform_name)}".strip()


def plan_caption(
    payload: RichMediaPayload, sender_label: str | None, platform_name: str, total_text: str | None = None
) -> CaptionPlan:
    """Move long descriptions into a separate text message using the existing header/footer fallback."""
    text = total_text if total_text is not None else build_caption(payload, sender_label, platform_name)
    if len(text) <= CAPTION_LIMIT:
        return CaptionPlan(text)
    footer = _footer(payload, platform_name)
    caption = f"{_header(payload, sender_label)}{footer}".strip()
    return CaptionPlan(footer if len(caption) > CAPTION_LIMIT else caption, text)
