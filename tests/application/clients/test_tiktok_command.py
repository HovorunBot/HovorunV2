"""Tests for TikTok rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.clients.tiktok import TikTokService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.sync_http import FakeSyncHTTP


@pytest.fixture
async def tiktok_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the TikTok RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.TIKTOK)


@pytest.mark.vcr
async def test_handle_tiktok_video(tiktok_command: RichMediaCommand, test_container: AsyncContainer) -> None:
    """Test handling a TikTok video link using real extraction and VCR."""
    chat_id = 456
    url = "https://www.tiktok.com/@fueltothe_max/video/7397394517311212818"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await test_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(test_container, chat_id)

    await tiktok_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    assert "Mock User" in kwargs["media"][0].caption
    assert "tiktok.com" in kwargs["media"][0].caption


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
