"""Tests for TikTok rich media extraction."""

import pytest

from hovorunv2.application.media.providers.tiktok import TikTokService
from tests.utils.sync_http import FakeSyncHTTP


@pytest.mark.unit
def test_tiktok_sync_extraction() -> None:
    """Test synchronous worker extraction for TikTok video."""
    service = TikTokService.sync()
    http = FakeSyncHTTP(
        json_responses={
            "www.tikwm.com/api/": {
                "code": 0,
                "data": {
                    "title": "Sync TikTok Video Title",
                    "author": {
                        "nickname": "TikTok Creator",
                        "unique_id": "creator123",
                    },
                    "play": "https://www.tikwm.com/video/media.mp4",
                    "digg_count": 1500,
                    "play_count": 25000,
                },
            }
        }
    )
    url = "https://www.tiktok.com/@creator123/video/7397394517311212818"
    payload = service.extract_sync(url, http)
    assert payload is not None
    assert payload.author_name == "TikTok Creator"
    assert payload.author_handle == "creator123"
    assert payload.content == "Sync TikTok Video Title"
    assert len(payload.media_items) == 1
    assert payload.media_items[0].url == "https://www.tikwm.com/video/media.mp4"
    assert payload.media_items[0].is_video is True
    assert "❤️ 1.5K" in payload.footer_text
    assert "👁️ 25K" in payload.footer_text


@pytest.mark.unit
def test_tiktok_sync_gallery_extraction() -> None:
    """Test synchronous worker extraction for TikTok photo gallery."""
    service = TikTokService.sync()
    http = FakeSyncHTTP(
        json_responses={
            "www.tikwm.com/api/": {
                "code": 0,
                "data": {
                    "title": "Photo Slideshow",
                    "author": {
                        "nickname": "Photo Creator",
                        "unique_id": "photocreator",
                    },
                    "images": [
                        "https://www.tikwm.com/photo/1.jpg",
                        "https://www.tikwm.com/photo/2.jpg",
                    ],
                    "digg_count": 50,
                    "play_count": 100,
                },
            }
        }
    )
    url = "https://www.tiktok.com/@photocreator/photo/7397394517311212819"
    payload = service.extract_sync(url, http)
    assert payload is not None
    assert payload.author_name == "Photo Creator"
    assert payload.author_handle == "photocreator"
    assert len(payload.media_items) == 2
    assert payload.media_items[0].url == "https://www.tikwm.com/photo/1.jpg"
    assert payload.media_items[0].is_video is False
    assert payload.media_items[1].url == "https://www.tikwm.com/photo/2.jpg"
    assert payload.media_items[1].is_video is False
