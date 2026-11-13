"""Tests for Facebook rich media extraction."""

import pytest

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.providers.base import FACEBOOK_CONFIG, BaseMediaClient
from tests.utils.sync_http import FakeSyncHTTP


@pytest.mark.unit
def test_facebook_sync_metadata_extraction() -> None:
    """Test synchronous OpenGraph metadata extraction for Facebook."""
    client = BaseMediaClient.for_worker(FACEBOOK_CONFIG)
    html = (
        "<!DOCTYPE html><html><head>"
        '<meta property="og:title" content="Facebook Creator - Inspiring Post">'
        '<meta property="og:description" content="This is an inspiring Facebook post content.">'
        '<meta property="og:image" content="https://scontent.facebook.com/photo123.jpg">'
        "</head><body></body></html>"
    )
    url = "https://www.facebook.com/share/p/1B37pk4bUf/"
    http = FakeSyncHTTP(text_responses={url: html})

    payload = client.extract_metadata_sync(url, http)
    assert payload is not None
    assert payload.author_name == "Facebook Creator"
    assert "Inspiring Post" in payload.content or "inspiring Facebook post" in payload.content
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://scontent.facebook.com/photo123.jpg"
    assert payload.media_items[0].is_video is False


@pytest.mark.unit
def test_facebook_sync_extract_media_tier_fallback() -> None:
    """Test multi-tier extraction falling back to Tier 4 OpenGraph when generic extractor fails."""
    client = BaseMediaClient.for_worker(FACEBOOK_CONFIG)
    html = (
        "<!DOCTYPE html><html><head>"
        '<meta property="og:title" content="Facebook User">'
        '<meta property="og:description" content="Fallback description text">'
        '<meta property="og:image" content="https://scontent.facebook.com/img.jpg">'
        "</head><body></body></html>"
    )
    url = "https://www.facebook.com/share/p/1B37pk4bUf/"
    http = FakeSyncHTTP(text_responses={url: html})

    class FailingExtractor(NativeMediaExtractor):
        def extract(
            self, url: str, cookies: dict[str, str] | None = None, *, deadline: ExtractionDeadline | None = None
        ) -> RichMediaPayload:
            msg = "generic failure"
            raise MediaExtractionError(msg)

    payload = client.extract_media_sync(url, http, FailingExtractor())
    assert payload is not None
    assert payload.author_name == "Facebook User"
    assert payload.media_items[0].url == "https://scontent.facebook.com/img.jpg"
