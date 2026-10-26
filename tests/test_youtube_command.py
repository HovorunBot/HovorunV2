"""Tests for YouTube rich media command."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import create_mock_message, get_media_command


@pytest.fixture
async def youtube_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the YouTube RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.YOUTUBE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.youtube.com/shorts/abc-123", True),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://youtu.be/dQw4w9WgXcQ", True),
        ("https://www.youtube.com/post/Ugkx1234567890abcdef", True),
        ("https://www.youtube.com/channel/UC12345/community?lb=Ugkx1234567890", True),
        ("https://www.youtube.com/@channel/community?lb=Ugkx1234567890", True),
        ("https://www.youtube.com/@channel/post/Ugkx1234567890abcdef", True),
        ("https://www.youtube.com/@channel", False),
        ("No link here", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    youtube_command: RichMediaCommand,
    text: str | None,
    expected: bool,
) -> None:
    """Test YouTube URL pattern matching for videos, Shorts, and Community posts."""
    message = create_mock_message(text)
    assert await youtube_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_youtube_community_post(youtube_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test extracting and sending a YouTube Community image+text post."""
    chat_id = 456
    url = "https://www.youtube.com/post/Ugkx1234567890abcdef"

    yt_initial_data = {
        "contents": {
            "backstagePostThreadRenderer": {
                "post": {
                    "backstagePostRenderer": {
                        "authorText": {"runs": [{"text": "Tech Channel"}]},
                        "authorEndpoint": {"commandMetadata": {"webCommandMetadata": {"url": "/@TechChannel"}}},
                        "contentText": {"runs": [{"text": "New community update with photo!"}]},
                        "voteCount": {"simpleText": "4.2K"},
                        "backstageAttachment": {
                            "backstageImageRenderer": {
                                "image": {
                                    "thumbnails": [
                                        {"url": "https://yt3.ggpht.com/small.jpg"},
                                        {"url": "https://yt3.ggpht.com/highres.jpg"},
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

    @asynccontextmanager
    async def mock_get(req_url: str, **_kwargs: Any) -> AsyncGenerator[MagicMock]:
        resp = MagicMock()
        resp.status = HTTPStatus.OK
        if "youtube.com/post/" in req_url:
            resp.text = AsyncMock(return_value=html_body)
        else:
            resp.json = AsyncMock(return_value=[[[""]], None, "en"])
        yield resp

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)

    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    with patch.object(aiohttp.ClientSession, "get", side_effect=mock_get):
        await youtube_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    media = kwargs["media"]
    assert len(media) == 1
    assert media[0].media == "https://yt3.ggpht.com/highres.jpg"
    assert "Tech Channel" in media[0].caption
    assert "New community update with photo!" in media[0].caption
    assert "❤️ 4.2K" in media[0].caption


@pytest.mark.asyncio
async def test_youtube_split_video_vs_post_toggles(
    youtube_command: RichMediaCommand, init_container: AsyncContainer
) -> None:
    """Test that YouTube video vs community post toggles filter independently."""
    chat_id = 654
    video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    chat_status_service = await init_container.get(ChatStatusService)
    command_service = await init_container.get(CommandService)
    await chat_status_service.approve(chat_id)
    await command_service.enable_command(chat_id, CommandName.YOUTUBE)
    await command_service.disable_command(chat_id, CommandName.YOUTUBE_VIDEO)

    mock_info = {
        "title": "Test Video",
        "description": "Test video description",
        "uploader": "Channel",
        "uploader_id": "channel",
        "uploader_url": "https://youtube.com/@channel",
        "url": "https://video.url/file.mp4",
        "vcodec": "avc1",
    }

    @asynccontextmanager
    async def mock_get(_req_url: str, **_kwargs: Any) -> AsyncGenerator[MagicMock]:
        resp = MagicMock()
        resp.status = HTTPStatus.OK
        resp.json = AsyncMock(return_value=[[[""]], None, "en"])
        yield resp

    message = create_mock_message(video_url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    with (
        patch("hovorunv2.application.media.extractor.yt_dlp.YoutubeDL.extract_info", return_value=mock_info),
        patch.object(aiohttp.ClientSession, "get", side_effect=mock_get),
    ):
        await youtube_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_not_called()


@pytest.mark.asyncio
async def test_handle_youtube_community_post_canonicalization(
    youtube_command: RichMediaCommand, init_container: AsyncContainer
) -> None:
    """Test that channel community URLs canonicalize to post URL."""
    chat_id = 789
    url = "https://www.youtube.com/channel/UC12345/community?lb=Ugkx9876543210zyx"
    requested_urls: list[str] = []

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

    @asynccontextmanager
    async def mock_get(req_url: str, **_kwargs: Any) -> AsyncGenerator[MagicMock]:
        requested_urls.append(req_url)
        resp = MagicMock()
        resp.status = HTTPStatus.OK
        if "youtube.com/post/" in req_url:
            resp.text = AsyncMock(return_value=html_body)
        else:
            resp.json = AsyncMock(return_value=[[[""]], None, "en"])
        yield resp

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)

    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    with patch.object(aiohttp.ClientSession, "get", side_effect=mock_get):
        await youtube_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    assert "https://www.youtube.com/post/Ugkx9876543210zyx" in requested_urls


@pytest.mark.asyncio
async def test_youtube_community_post_skips_ytdlp(
    youtube_command: RichMediaCommand, init_container: AsyncContainer
) -> None:
    """Test that failed community post extraction skips yt-dlp fallback."""
    chat_id = 999
    url = "https://www.youtube.com/post/UgkxInvalidPost12345"

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)

    @asynccontextmanager
    async def mock_get(_req_url: str, **_kwargs: Any) -> AsyncGenerator[MagicMock]:
        resp = MagicMock()
        resp.status = HTTPStatus.NOT_FOUND
        resp.text = AsyncMock(return_value="Not found")
        yield resp

    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    mock_extract_info = MagicMock()

    with (
        patch("hovorunv2.application.media.extractor.yt_dlp.YoutubeDL.extract_info", mock_extract_info),
        patch.object(aiohttp.ClientSession, "get", side_effect=mock_get),
    ):
        await youtube_command.handle(message, cast("Bot", bot))

    mock_extract_info.assert_not_called()
    bot.send_media_group.assert_not_called()
