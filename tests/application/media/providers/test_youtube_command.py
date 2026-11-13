"""Tests for YouTube rich media extraction."""

import json

import pytest

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.providers.youtube import YoutubeService
from tests.utils.sync_http import FakeSyncHTTP


@pytest.mark.unit
def test_youtube_sync_community_post_canonicalization_and_skip_ytdlp() -> None:
    """Channel community URLs canonicalize to post URL, and failed posts skip yt-dlp."""
    service = YoutubeService.sync()
    channel_url = "https://www.youtube.com/channel/UC12345/community?lb=Ugkx9876543210zyx"
    post_url = "https://www.youtube.com/post/Ugkx9876543210zyx"
    yt_initial_data = {
        "contents": {
            "backstagePostThreadRenderer": {
                "post": {
                    "backstagePostRenderer": {
                        "authorText": {"runs": [{"text": "Science Channel"}]},
                        "contentText": {"runs": [{"text": "Canonical community test"}]},
                        "backstageAttachment": {
                            "backstageImageRenderer": {
                                "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/img.jpg"}]}
                            }
                        },
                    }
                }
            }
        }
    }
    html_body = f"<html><script>var ytInitialData = {json.dumps(yt_initial_data)};</script></html>"
    http = FakeSyncHTTP(text_responses={post_url: html_body})
    payload = service.extract_sync(channel_url, http)
    assert payload is not None
    assert payload.author_name == "Science Channel"
    assert any(post_url in str(req.get("url", "")) for req in http.requests)

    called_extractor = False

    class ForbiddenExtractor(NativeMediaExtractor):
        def extract(
            self, url: str, cookies: dict[str, str] | None = None, *, deadline: ExtractionDeadline | None = None
        ) -> RichMediaPayload:
            nonlocal called_extractor
            called_extractor = True
            msg = "yt-dlp must not be called for community posts"
            raise AssertionError(msg)

    invalid_post = "https://www.youtube.com/post/UgkxInvalidPost12345"
    with pytest.raises(MediaExtractionError):
        service.extract_media_sync(invalid_post, FakeSyncHTTP(), ForbiddenExtractor())
    assert called_extractor is False


@pytest.mark.unit
def test_youtube_sync_community_post_extraction() -> None:
    """Test synchronous worker extraction for YouTube Community post."""
    service = YoutubeService.sync()
    post_url = "https://www.youtube.com/post/Ugkx1234567890abcdef"
    yt_initial_data = {
        "contents": {
            "backstagePostThreadRenderer": {
                "post": {
                    "backstagePostRenderer": {
                        "authorText": {"runs": [{"text": "Tech Creator"}]},
                        "authorEndpoint": {"commandMetadata": {"webCommandMetadata": {"url": "/@TechCreator"}}},
                        "contentText": {"runs": [{"text": "Sync worker update!"}]},
                        "voteCount": {"simpleText": "1.2K"},
                        "backstageAttachment": {
                            "backstageImageRenderer": {
                                "image": {
                                    "thumbnails": [
                                        {"url": "https://yt3.ggpht.com/sync_img.jpg"},
                                    ]
                                }
                            }
                        },
                    }
                }
            }
        }
    }
    html_body = f"<html><script>var ytInitialData = {json.dumps(yt_initial_data)};</script></html>"
    http = FakeSyncHTTP(text_responses={post_url: html_body})

    payload = service.extract_sync(post_url, http)
    assert payload is not None
    assert payload.author_name == "Tech Creator"
    assert payload.author_handle == "TechCreator"
    assert payload.content == "Sync worker update!"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://yt3.ggpht.com/sync_img.jpg"
    assert "❤️ 1.2K" in payload.footer_text


@pytest.mark.unit
def test_youtube_sync_video_url_skips_direct_and_uses_extractor() -> None:
    """Test that YouTube video URLs skip direct HTML extraction and delegate to NativeMediaExtractor."""
    service = YoutubeService.sync()
    video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    http = FakeSyncHTTP()

    expected_payload = RichMediaPayload(
        author_name="Rick Astley",
        author_handle="RickAstleyVEVO",
        author_url="https://youtube.com/@RickAstleyVEVO",
        content="Never Gonna Give You Up",
        footer_text="👁️ 1B",
        original_url=video_url,
        media_items=[MediaItem(url="https://googlevideo.com/videoplayback", is_video=True)],
    )

    class DummyExtractor(NativeMediaExtractor):
        def extract(
            self, url: str, cookies: dict[str, str] | None = None, *, deadline: ExtractionDeadline | None = None
        ) -> RichMediaPayload:
            return expected_payload

    # Direct extraction returns None for standard video URLs
    assert service.extract_sync(video_url, http) is None

    # Multi-tier extraction delegates to generic extractor
    payload = service.extract_media_sync(video_url, http, DummyExtractor())
    assert payload == expected_payload
