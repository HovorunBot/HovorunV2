"""Tests for Facebook rich media command."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.enums import ChatStatus, CommandName
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import create_mock_message, get_media_command


@pytest.fixture
async def facebook_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Facebook RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.FACEBOOK)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.facebook.com/user/posts/12345", True),
        ("https://www.facebook.com/groups/id/posts/12345", True),
        ("https://www.facebook.com/permalink.php?story_fbid=123", True),
        ("https://www.facebook.com/photo.php?fbid=123", True),
        ("https://www.facebook.com/reel/123", True),
        ("https://www.facebook.com/watch/?v=123", True),
        ("https://www.facebook.com/share/p/1FuWE1yAik/", True),
        ("No link here", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    facebook_command: RichMediaCommand,
    text: str | None,
    expected: bool,
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await facebook_command.is_triggered(message) == expected


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_facebook_photo_post(facebook_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling a Facebook photo post using real extraction and VCR."""
    chat_id = 789
    url = "https://www.facebook.com/share/p/1B37pk4bUf/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    await facebook_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "facebook.com" in caption


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_facebook_video_fallback(
    facebook_command: RichMediaCommand, init_container: AsyncContainer
) -> None:
    """Test handling a Facebook video using real extraction and VCR (yt-dlp fallback)."""
    chat_id = 789
    url = "https://www.facebook.com/share/v/1NBDyTuecs/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    await facebook_command.handle(message, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "facebook.com" in caption
