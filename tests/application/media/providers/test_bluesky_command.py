"""Tests for Bluesky rich media extraction."""

import pytest

from hovorunv2.application.media.providers.bluesky import BlueskyService
from tests.utils.sync_http import FakeSyncHTTP


@pytest.mark.unit
def test_bluesky_sync_extraction() -> None:
    """Test synchronous worker extraction for Bluesky post."""
    service = BlueskyService.sync()
    http = FakeSyncHTTP(
        json_responses={
            "public.api.bsky.app/xrpc/app.bsky.actor.getProfile": {
                "did": "did:plc:12345abcdef",
            },
            "public.api.bsky.app/xrpc/app.bsky.feed.getPostThread": {
                "thread": {
                    "post": {
                        "author": {
                            "displayName": "Bluesky Author",
                            "handle": "author.bsky.social",
                        },
                        "record": {
                            "text": "Hello Bluesky sync worker!",
                        },
                        "replyCount": 3,
                        "repostCount": 7,
                        "likeCount": 42,
                        "embed": {
                            "$type": "app.bsky.embed.images#view",
                            "images": [
                                {
                                    "fullsize": "https://cdn.bsky.app/img/full.jpg",
                                    "alt": "Bluesky photo",
                                }
                            ],
                        },
                    }
                }
            },
        }
    )
    url = "https://bsky.app/profile/author.bsky.social/post/3mlmib6xdbc2a"
    payload = service.extract_sync(url, http)
    assert payload is not None
    assert payload.author_name == "Bluesky Author"
    assert payload.author_handle == "author.bsky.social"
    assert payload.content == "Hello Bluesky sync worker!"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://cdn.bsky.app/img/full.jpg"
    assert payload.media_items[0].is_video is False
    assert "💬 3" in payload.footer_text
    assert "🔁 7" in payload.footer_text
    assert "❤️ 42" in payload.footer_text
