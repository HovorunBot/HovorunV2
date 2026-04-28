"""Tests for MediaExtractor."""

from unittest.mock import MagicMock, patch

import pytest
from dishka import AsyncContainer

from hovorunv2.application.media.extractor import MediaExtractor


@pytest.mark.asyncio
async def test_extract_payload_youtube_shorts(init_container: AsyncContainer) -> None:
    """Test extracting payload from YouTube Shorts."""
    # Use real translation service from test container

    mock_info = {
        "title": "Short Video Title",
        "description": "Short desc",  # len 10
        "uploader": "Uploader Name",
        "uploader_id": "uploader123",
        "uploader_url": "https://youtube.com/uploader123",
        "url": "https://video.url/file.mp4",
        "like_count": 100,
        "view_count": 1000,
    }

    media_extractor = await init_container.get(MediaExtractor)
    with patch("hovorunv2.application.media.extractor.yt_dlp.YoutubeDL.extract_info", return_value=mock_info):
        payload = await media_extractor.extract_payload(
            session=MagicMock(), url="https://youtube.com/shorts/abc-123", chat_id=123, platform="telegram"
        )

        assert payload is not None
        assert payload.author_name == "Uploader Name"
        assert payload.is_video is True
        # description "Short desc" is len 10, so it should use title "Short Video Title"
        assert "Short Video Title" in payload.content
        assert "https://video.url/file.mp4" in payload.media_urls


@pytest.mark.asyncio
async def test_extract_payload_failure(init_container: AsyncContainer) -> None:
    """Test service behavior when yt-dlp fails."""
    media_extractor = await init_container.get(MediaExtractor)
    with patch(
        "hovorunv2.application.media.extractor.yt_dlp.YoutubeDL.extract_info",
        side_effect=Exception("Failed"),
    ):
        payload = await media_extractor.extract_payload(
            session=MagicMock(), url="https://invalid.url", chat_id=123, platform="telegram"
        )
        assert payload is None
