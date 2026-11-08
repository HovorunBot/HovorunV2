"""Pure shared Telegram caption and quote rendering, independent of transports."""

import html
from dataclasses import dataclass
from urllib.parse import urlsplit

from hovorunv2.application.media.payloads import RichMediaPayload

CAPTION_LIMIT = 1024
HEADER_TEMPLATE = "🗣 <b>{tg_user_name}</b> shared a post by <b>{author_name}</b> ({author_link})\n\n"
QUOTE_TEMPLATE = "<blockquote>🔄 <b>{author_name}</b> ({author_link})\n{content}</blockquote>\n"
METRICS_TEMPLATE = "{metrics}\n"
FOOTER_TEMPLATE = "🔗 {link}"
MEDIA_UNAVAILABLE_TEXT = "⚠️ Media could not be extracted. View the original post using the link below."


@dataclass(frozen=True, slots=True)
class CaptionPlan:
    """A bounded media caption and optional preceding text message."""

    caption: str
    separate_text: str | None = None


def _safe_link(url: str, label: str) -> str:
    """Render a safe HTML anchor tag or fall back to plain label for invalid or non-HTTP URLs.

    Args:
        url: Untrusted URL to validate and escape.
        label: Pre-escaped or raw HTML label to wrap.

    Returns:
        Anchor tag with quote-escaped href, or label if URL is not a valid HTTP(S) link.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return label
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return label
    return f'<a href="{html.escape(url, quote=True)}">{label}</a>'


def _header(payload: RichMediaPayload, sender_label: str | None) -> str:
    author_link = _safe_link(payload.author_url, f"@{payload.author_handle}")
    return HEADER_TEMPLATE.format(
        tg_user_name=html.escape(sender_label or "Someone"),
        author_name=payload.author_name,
        author_link=author_link,
    )


def _footer(payload: RichMediaPayload, platform_name: str) -> str:
    link = _safe_link(payload.original_url, f"Open on {html.escape(platform_name)}")
    return FOOTER_TEMPLATE.format(link=link)


def build_caption(payload: RichMediaPayload, sender_label: str | None, platform_name: str) -> str:
    """Build the media caption while escaping only the transient sender label.

    Args:
        payload: Payload carrying the prepared content and quote.
        sender_label: Label for the sender sharing the media.
        platform_name: Platform name for the footer link.

    Returns:
        Assembled caption text.
    """
    content = payload.content + "\n\n" if payload.content else ""
    if payload.video_unavailable:
        content += MEDIA_UNAVAILABLE_TEXT + "\n\n"
    quote = ""
    if payload.quoted_payload:
        quoted_author_link = _safe_link(payload.quoted_payload.author_url, f"@{payload.quoted_payload.author_handle}")
        quote = QUOTE_TEMPLATE.format(
            author_name=payload.quoted_payload.author_name,
            author_link=quoted_author_link,
            content=payload.quoted_payload.content,
        )
    metrics = ""
    if payload.footer_text and payload.footer_text.strip():
        metrics = METRICS_TEMPLATE.format(metrics=payload.footer_text.strip().replace("\n", " "))
    return f"{_header(payload, sender_label)}{content}{quote}{metrics}{_footer(payload, platform_name)}".strip()


def plan_caption(
    payload: RichMediaPayload, sender_label: str | None, platform_name: str, total_text: str | None = None
) -> CaptionPlan:
    """Plan a caption, moving overlong text into a separate text message.

    Args:
        payload: Payload carrying the prepared content and quote.
        sender_label: Label for the sender sharing the media.
        platform_name: Platform name for the footer link.
        total_text: Optional prebuilt caption text to plan against.

    Returns:
        Caption plan with an optional separate text message.
    """
    text = total_text if total_text is not None else build_caption(payload, sender_label, platform_name)
    if len(text) <= CAPTION_LIMIT:
        return CaptionPlan(text)
    footer = _footer(payload, platform_name)
    caption = f"{_header(payload, sender_label)}{footer}".strip()
    return CaptionPlan(footer if len(caption) > CAPTION_LIMIT else caption, text)
