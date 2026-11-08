"""Tests for pure shared Telegram caption and quote rendering security and formatting."""

import pytest

from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.rendering import build_caption, plan_caption


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
