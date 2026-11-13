"""Pure shared Telegram caption and quote rendering, independent of transports."""

import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from hovorunv2.application.media.payloads import RichMediaPayload

CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_LIMIT = 4096
HEADER_TEMPLATE = "🗣 <b>{tg_user_name}</b> shared a post by <b>{author_name}</b> ({author_link})\n\n"
QUOTE_TEMPLATE = "<blockquote>🔄 <b>{author_name}</b> ({author_link})\n{content}</blockquote>\n"
METRICS_TEMPLATE = "{metrics}\n"
FOOTER_TEMPLATE = "🔗 {link}"
MEDIA_UNAVAILABLE_TEXT = "⚠️ Media could not be extracted. View the original post using the link below."

_TAG_TOKEN_RE = re.compile(r"^</?([a-zA-Z0-9]+)(?:\s+[^>]*)?>$")
_TOKEN_RE = re.compile(r"<[^>]+>|&[a-zA-Z0-9#]+;|\n\n|\n|\s+|[^\s<&]+")
_RENDERED_HEADER_RE = re.compile(r"^🗣 [^\n]+ shared a post by [^\n]+\n\n")
_RENDERED_FOOTER_RE = re.compile(r"\n🔗 [^\n]+$")
_RENDERED_METRICS_RE = re.compile(r"\n(?:[^\w\s]+(?:\s+[\d,.KMkmBb]+)+\s*)+$")


@dataclass(frozen=True, slots=True)
class CaptionPlan:
    """A bounded media caption and optional preceding text message."""

    caption: str
    separate_text: str | None = None


def _tokenize_html(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    length = len(text)
    while pos < length:
        match = _TOKEN_RE.match(text, pos)
        if match:
            tokens.append(match.group(0))
            pos = match.end()
        else:
            tokens.append(text[pos])
            pos += 1
    return tokens


def _tag_name_and_type(token: str) -> tuple[bool, str, bool]:
    if not (token.startswith("<") and token.endswith(">")):
        return False, "", False
    if token.endswith("/>"):
        return False, "", False
    match = _TAG_TOKEN_RE.match(token)
    if not match:
        return False, "", False
    return True, match.group(1).lower(), token.startswith("</")


def _update_open_tags(open_tags: list[tuple[str, str]], token: str) -> None:
    is_tag, name, is_closing = _tag_name_and_type(token)
    if not is_tag:
        return
    if is_closing:
        for idx in range(len(open_tags) - 1, -1, -1):
            if open_tags[idx][0] == name:
                del open_tags[idx:]
                break
    else:
        open_tags.append((name, token))


def _tag_boundary_strings(open_tags: list[tuple[str, str]]) -> tuple[str, str]:
    prefix = "".join(token for _, token in open_tags)
    suffix = "".join(f"</{name}>" for name, _ in reversed(open_tags))
    return prefix, suffix


def _find_best_break_point(tokens: list[str]) -> int | None:
    for idx in range(len(tokens) - 1, 0, -1):
        if "\n\n" in tokens[idx]:
            return idx
    for idx in range(len(tokens) - 1, 0, -1):
        if "\n" in tokens[idx]:
            return idx
    for idx in range(len(tokens) - 1, 0, -1):
        if tokens[idx].isspace():
            return idx
    return None


def _build_next_chunk(tokens: list[str], open_tags: list[tuple[str, str]], limit: int) -> tuple[str, int]:
    prefix, _ = _tag_boundary_strings(open_tags)
    collected: list[str] = []
    tentative_tags = list(open_tags)
    current_len = len(prefix)

    for idx, token in enumerate(tokens):
        next_tentative = list(tentative_tags)
        _update_open_tags(next_tentative, token)
        _, next_suffix = _tag_boundary_strings(next_tentative)
        candidate_len = current_len + len(token) + len(next_suffix)

        if candidate_len <= limit:
            collected.append(token)
            tentative_tags = next_tentative
            current_len += len(token)
            continue

        if not collected:
            avail = max(1, limit - len(prefix) - len(next_suffix))
            sliced = token[:avail]
            tokens[idx] = token[avail:]
            collected.append(sliced)
            _update_open_tags(tentative_tags, sliced)
            _, suffix = _tag_boundary_strings(tentative_tags)
            del open_tags[:]
            open_tags.extend(tentative_tags)
            return prefix + "".join(collected) + suffix, idx

        break_idx = _find_best_break_point(collected)
        if break_idx is not None and break_idx > 0:
            final_collected = collected[:break_idx]
            recomputed_tags = list(open_tags)
            for t in final_collected:
                _update_open_tags(recomputed_tags, t)
            _, suffix = _tag_boundary_strings(recomputed_tags)
            del open_tags[:]
            open_tags.extend(recomputed_tags)
            return prefix + "".join(final_collected) + suffix, break_idx + 1

        _, suffix = _tag_boundary_strings(tentative_tags)
        del open_tags[:]
        open_tags.extend(tentative_tags)
        return prefix + "".join(collected) + suffix, idx

    _, suffix = _tag_boundary_strings(tentative_tags)
    del open_tags[:]
    open_tags.extend(tentative_tags)
    return prefix + "".join(collected) + suffix, len(tokens)


def split_html_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> tuple[str, ...]:
    """Split an HTML message into well-formed chunks respecting Telegram limits.

    Args:
        text: HTML-formatted text to split.
        limit: Maximum character length per chunk.

    Returns:
        Tuple of balanced HTML chunks, each under the limit.
    """
    if not text or len(text) <= limit:
        return (text,)
    tokens = _tokenize_html(text)
    chunks: list[str] = []
    open_tags: list[tuple[str, str]] = []

    while tokens:
        if chunks and tokens and tokens[0].isspace():
            tokens.pop(0)
            if not tokens:
                break
        chunk, consumed = _build_next_chunk(tokens, open_tags, limit)
        if chunk:
            chunks.append(chunk)
        if consumed > 0:
            del tokens[:consumed]
        elif not tokens:
            break

    return tuple(chunks) if chunks else (text,)


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
    fallback = footer if len(caption) > CAPTION_LIMIT else caption
    if len(fallback) > CAPTION_LIMIT:
        fallback = fallback[:CAPTION_LIMIT]
    return CaptionPlan(fallback, text)


def extract_rendered_post_content(text: str) -> str:
    """Extract only post content from a bot-rendered media message when present.

    Args:
        text: Plain message or caption text from a replied Telegram message.

    Returns:
        Post content without the bot header, metrics, unavailable notice, or footer link,
        or the original text when it does not match the rendered post envelope.
    """
    header_match = _RENDERED_HEADER_RE.match(text)
    footer_match = _RENDERED_FOOTER_RE.search(text)
    if header_match is None or footer_match is None:
        return text
    body = text[header_match.end() : footer_match.start()]
    body = _RENDERED_METRICS_RE.sub("", body)
    stripped = body.strip()
    if stripped.endswith(MEDIA_UNAVAILABLE_TEXT):
        stripped = stripped.removesuffix(MEDIA_UNAVAILABLE_TEXT).strip()
    return stripped or text
