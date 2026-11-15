"""Tests for HTML-aware message splitting across Telegram limit boundaries."""

import pytest

from hovorunv2.application.media.rendering import split_html_message


@pytest.mark.unit
def test_split_html_message_under_limit_returns_single_chunk() -> None:
    """Messages within the limit are returned as a single-element tuple without changes."""
    text = "<b>Hello</b> world!"
    assert split_html_message(text, limit=4096) == (text,)
    assert split_html_message("", limit=4096) == ("",)


@pytest.mark.unit
def test_split_html_message_balances_tags_across_chunks() -> None:
    """Overlong formatted text is split into chunks where every tag is closed and reopened."""
    paragraph1 = "<b>" + "word " * 500 + "</b>"
    paragraph2 = "<blockquote>" + "quote " * 500 + "</blockquote>"
    full_text = f"{paragraph1}\n\n{paragraph2}"
    assert len(full_text) > 4096

    chunks = split_html_message(full_text, limit=4096)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 4096
        # Balanced bold tags
        assert chunk.count("<b>") == chunk.count("</b>")
        # Balanced blockquote tags
        assert chunk.count("<blockquote>") == chunk.count("</blockquote>")


@pytest.mark.unit
def test_split_html_message_preserves_entities_at_boundary() -> None:
    """Splitting never cuts across HTML entities like &amp; or &#1234;."""
    prefix = "x" * 4090
    text = f"{prefix}&amp;extra text"
    chunks = split_html_message(text, limit=4096)
    assert len(chunks) == 2
    assert chunks[0].endswith("&amp;") or not chunks[0].endswith("&")
    assert not chunks[1].startswith("amp;")


@pytest.mark.unit
def test_split_html_message_handles_giant_unbroken_words() -> None:
    """Unbroken text without whitespace is sliced into valid bounded chunks."""
    text = "a" * 10000
    chunks = split_html_message(text, limit=4096)
    assert len(chunks) == 3
    assert chunks[0] == "a" * 4096
    assert chunks[1] == "a" * 4096
    assert chunks[2] == "a" * 1808
    assert "".join(chunks) == text


@pytest.mark.unit
def test_split_html_message_measures_unescaped_utf16_and_preserves_whitespace_tokens() -> None:
    """Measure unescaped UTF-16 units, avoid cutting inside tags/entities, and keep post-break newlines."""
    # Surrogate pairs (2 UTF-16 units each): 6 emojis = 12 UTF-16 units, even though len() is 6
    emojis = "😀" * 6
    chunks = split_html_message(emojis, limit=8)
    assert chunks == ("😀😀😀😀", "😀😀")

    # Long HTML attribute should not trigger splitting when rendered text fits within limit
    long_url_html = f'<a href="https://example.com/{"x" * 200}">Short</a>'
    assert split_html_message(long_url_html, limit=20) == (long_url_html,)

    # Entities must not be split mid-entity when measuring UTF-16
    entity_text = "abcd&amp;efgh"
    entity_chunks = split_html_message(entity_text, limit=5)
    assert entity_chunks == ("abcd&amp;", "efgh")

    # Whitespace break followed by another whitespace token must not double-consume the second token
    assert split_html_message("aaaa\n\n\nbbbb", limit=6) == ("aaaa", "\nbbbb")
