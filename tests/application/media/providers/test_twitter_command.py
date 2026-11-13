"""Tests for Twitter rich media extraction."""

import pytest

from hovorunv2.application.media.providers.twitter import TwitterService
from tests.utils.sync_http import FakeSyncHTTP


@pytest.mark.unit
def test_twitter_sync_extraction() -> None:
    """Test synchronous worker extraction for Twitter posts."""
    service = TwitterService.sync()
    http = FakeSyncHTTP(
        json_responses={
            "api.vxtwitter.com/i/status/12345": {
                "text": "Hello world from sync Twitter!",
                "user_name": "Sync User",
                "user_screen_name": "syncuser",
                "retweets": 15,
                "likes": 200,
                "replies": 8,
                "media_extended": [{"url": "https://video.twimg.com/sync_vid.mp4", "type": "video"}],
            }
        }
    )
    url = "https://x.com/syncuser/status/12345"
    payload = service.extract_sync(url, http)
    assert payload is not None
    assert payload.author_name == "Sync User"
    assert payload.author_handle == "syncuser"
    assert payload.content == "Hello world from sync Twitter!"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://video.twimg.com/sync_vid.mp4"
    assert payload.media_items[0].is_video is True
    assert "🔁 15" in payload.footer_text
    assert "❤️ 200" in payload.footer_text


@pytest.mark.unit
def test_twitter_sync_quote_tweet() -> None:
    """Test synchronous worker extraction for Twitter quote tweets."""
    service = TwitterService.sync()
    http = FakeSyncHTTP(
        json_responses={
            "api.vxtwitter.com/i/status/999": {
                "text": "My commentary https://t.co/quote123",
                "qrtURL": "https://t.co/quote123",
                "user_name": "Commenter",
                "user_screen_name": "commenter",
                "retweets": 0,
                "likes": 5,
                "replies": 0,
                "media_extended": [],
                "qrt": {
                    "text": "Original tweet",
                    "user_name": "Original Author",
                    "user_screen_name": "orig",
                    "media_extended": [{"url": "https://pbs.twimg.com/img.jpg", "type": "image"}],
                },
            }
        }
    )
    payload = service.extract_sync("https://twitter.com/commenter/status/999", http)
    assert payload is not None
    assert payload.content == "My commentary"
    assert payload.quoted_payload is not None
    assert payload.quoted_payload.content == "Original tweet"
    assert payload.quoted_payload.author_handle == "orig"
    assert len(payload.quoted_payload.media_items) == 1
