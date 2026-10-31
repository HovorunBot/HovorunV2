"""Shared bot/native caption policy preserves escaping and provider-specific quoted media behavior."""

import re

import pytest

from hovorunv2.application.media.finalization import (
    QUOTED_MEDIA_NOTE,
    append_translation,
    escape_payload_authors,
    merge_quoted_media,
    prepare_caption_text,
)
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.provider_config import ProviderConfig
from hovorunv2.application.translation.translation_rules import TranslationResult
from hovorunv2.domain.enums import CommandName


@pytest.mark.parametrize(
    ("merge", "only_empty", "has_own", "merged"),
    [
        (True, False, True, True),
        (True, True, True, False),
        (True, True, False, True),
        (False, False, False, False),
    ],
)
def test_quote_merge_policy_preserves_media_order_and_notice(
    *, merge: bool, only_empty: bool, has_own: bool, merged: bool
) -> None:
    """Quoted attachments follow existing provider policy and never replace the original source identity."""
    config = ProviderConfig(
        CommandName.TWITTER,
        re.compile(r"https://synthetic.invalid/post"),
        merge_quoted_media=merge,
        merge_quoted_only_when_empty=only_empty,
    )
    quoted_item = MediaItem("https://synthetic.invalid/quoted.jpg", is_video=False)
    quote = RichMediaPayload(
        "<quoted author>", "quoted&handle", "https://synthetic.invalid", "quoted caption", media_items=[quoted_item]
    )
    own = MediaItem("https://synthetic.invalid/own.mp4", is_video=True)
    payload = RichMediaPayload(
        "<author>",
        "author&handle",
        "https://synthetic.invalid",
        "caption",
        media_items=[own] if has_own else [],
        quoted_payload=quote,
    )
    escape_payload_authors(payload, "https://synthetic.invalid/post")
    merge_quoted_media(payload, config)
    assert payload.author_name == "&lt;author&gt;"
    assert quote.author_name == "&lt;quoted author&gt;"
    assert payload.author_handle == "author&amp;handle"
    assert quote.author_handle == "quoted&amp;handle"
    assert payload.original_url == "https://synthetic.invalid/post"
    assert payload.media_items == ([own] if has_own else []) + ([quoted_item] if merged else [])
    assert payload.content == "caption" + (QUOTED_MEDIA_NOTE if merged else "")


def test_caption_preparation_and_translation_escape_provider_markup() -> None:
    """Raw provider text and translated markup remain text inside the shared display block."""
    content = prepare_caption_text("<synthetic & caption>")
    assert content == "&lt;synthetic &amp; caption&gt;"
    assert append_translation(content, None) == content
    translated = TranslationResult("<translated & caption>", "uk", "🇫🇷")
    assert append_translation(content, translated) == (
        "&lt;synthetic &amp; caption&gt;\n\n🇫🇷 <b>Translated:</b>\n&lt;translated &amp; caption&gt;"
    )
