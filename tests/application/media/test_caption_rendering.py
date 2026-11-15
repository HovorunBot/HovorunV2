"""Tests for pure shared Telegram caption and quote rendering security and formatting."""

import pytest

from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.rendering import (
    CAPTION_LIMIT,
    build_caption,
    extract_rendered_post_content,
    plan_caption,
)


@pytest.mark.unit
def test_build_caption_escapes_hostile_url_attributes() -> None:
    """Hostile URLs cannot break out of HTML attributes or inject tags into captions."""
    payload = RichMediaPayload(
        author_name="Alice",
        author_handle="alice",
        author_url='https://example.com/user/"><b>injected</b><a href="',
        content="Hello world",
        original_url='https://example.com/post?a="&b=<tag>',
    )
    caption = build_caption(payload, sender_label='Bob "The Builder"', platform_name="Twitter")

    assert "<b>injected</b>" not in caption
    assert "<tag>" not in caption
    assert 'href="https://example.com/user/&quot;&gt;&lt;b&gt;injected&lt;/b&gt;&lt;a href=&quot;"' in caption
    assert 'href="https://example.com/post?a=&quot;&amp;b=&lt;tag&gt;"' in caption


@pytest.mark.unit
def test_build_caption_rejects_non_http_author_and_original_urls() -> None:
    """Non-HTTP(S) URLs degrade to plain text without clickable links or script execution."""
    quote = RichMediaPayload(
        author_name="Quoted Author",
        author_handle="quoted_handle",
        author_url="javascript:alert('quote')",
        content="Quoted text",
    )
    payload = RichMediaPayload(
        author_name="Main Author",
        author_handle="main_handle",
        author_url="javascript:alert('main')",
        content="Main content",
        original_url="data:text/html,<script>alert(1)</script>",
        quoted_payload=quote,
    )
    caption = build_caption(payload, sender_label="Sender", platform_name="Twitter")

    assert "javascript:" not in caption
    assert "data:" not in caption
    assert "<a href=" not in caption
    assert "(@main_handle)" in caption
    assert "(@quoted_handle)" in caption
    assert "🔗 Open on Twitter" in caption


@pytest.mark.unit
def test_build_caption_preserves_valid_urls() -> None:
    """Well-formed HTTP(S) links render expected anchor tags and preserve layout."""
    quote = RichMediaPayload(
        author_name="Quoted",
        author_handle="quoted",
        author_url="https://x.com/quoted",
        content="Quote text",
    )
    payload = RichMediaPayload(
        author_name="Author",
        author_handle="author",
        author_url="https://x.com/author",
        content="Post text",
        original_url="https://x.com/author/status/123",
        quoted_payload=quote,
        footer_text="❤️ 10",
    )
    caption = build_caption(payload, sender_label="User", platform_name="Twitter")

    expected = (
        '🗣 <b>User</b> shared a post by <b>Author</b> (<a href="https://x.com/author">@author</a>)\n\n'
        "Post text\n\n"
        '<blockquote>🔄 <b>Quoted</b> (<a href="https://x.com/quoted">@quoted</a>)\nQuote text</blockquote>\n'
        "❤️ 10\n"
        '🔗 <a href="https://x.com/author/status/123">Open on Twitter</a>'
    )
    assert caption == expected


@pytest.mark.unit
def test_plan_caption_with_hostile_urls() -> None:
    """Planning a caption preserves safe escaping in both short and overlong forms."""
    payload = RichMediaPayload(
        author_name="Author",
        author_handle="author",
        author_url='https://example.com/user/"><b>bad</b>',
        content="Short content",
        original_url='https://example.com/post?id="123"',
    )
    plan = plan_caption(payload, sender_label="Sender", platform_name="Twitter")
    assert plan.separate_text is None
    assert "<b>bad</b>" not in plan.caption
    assert 'href="https://example.com/user/&quot;&gt;&lt;b&gt;bad&lt;/b&gt;"' in plan.caption


@pytest.mark.unit
def test_plan_caption_measures_unescaped_utf16_and_never_slices_tags_or_entities() -> None:
    """Measure rendered UTF-16 units and avoid slicing HTML tags or entities when fallback exceeds limit."""
    # Surrogate pairs: 520 emojis = 1040 UTF-16 units > 1024, even though len() < 1024
    emoji_payload = RichMediaPayload(
        author_name="Author",
        author_handle="author",
        author_url="https://x.com/author",
        content="😀" * 520,
        original_url="https://x.com/author/1",
    )
    emoji_plan = plan_caption(emoji_payload, sender_label="User", platform_name="X")
    assert emoji_plan.separate_text is not None

    # Long URL in footer + HTML tags should not trigger split when unescaped plain text <= 1024
    long_url_payload = RichMediaPayload(
        author_name="Author",
        author_handle="author",
        author_url="https://x.com/" + "a" * 600,
        content="Short &amp; sweet",
        original_url="https://x.com/" + "b" * 600,
    )
    url_plan = plan_caption(long_url_payload, sender_label="User", platform_name="X")
    assert url_plan.separate_text is None

    # Even if fallback caption exceeds CAPTION_LIMIT, fallback must stay valid HTML without broken tags/entities
    huge_platform_payload = RichMediaPayload(
        author_name="Author",
        author_handle="author",
        author_url="https://x.com/author",
        content="x" * (CAPTION_LIMIT + 100),
        original_url="https://x.com/author/1",
    )
    huge_plan = plan_caption(huge_platform_payload, sender_label="User", platform_name="P&" + "x" * 1050)
    assert huge_plan.separate_text is not None
    assert huge_plan.caption.count("<a ") == huge_plan.caption.count("</a>")
    assert "&amp;" in huge_plan.caption or not huge_plan.caption.endswith("&")


@pytest.mark.unit
def test_extract_rendered_post_content_strips_quotes_translations_and_empty_split_captions() -> None:
    """Strip QUOTE_TEMPLATE headers, prior append_translation blocks, and empty split captions."""
    rendered_with_quote_and_translation = (
        "🗣 User shared a post by Author (@author)\n\n"
        "Original post text\n\n"
        "🇺🇦 Translated:\n"
        "Оригінальний текст\n\n"
        "🔄 Quoted (@quoted)\n"
        "Original quote text\n\n"
        "🇺🇦 Translated:\n"
        "Оригінальна цитата\n"
        "❤️ 10\n"
        "🔗 Open on X"
    )
    assert extract_rendered_post_content(rendered_with_quote_and_translation) == (
        "Original post text\n\nOriginal quote text"
    )

    header_footer_only = "🗣 User shared a post by Author (@author)\n\n🔗 Open on X"
    assert extract_rendered_post_content(header_footer_only) == ""
