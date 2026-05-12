"""Tests for the FacebookCommand class."""

from http import HTTPStatus
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.interface.telegram.handlers.facebook import FacebookCommand


@pytest.fixture
async def facebook_command(init_container: AsyncContainer) -> FacebookCommand:
    """Fixture to provide a FacebookCommand instance."""
    return await init_container.get(FacebookCommand)


def create_mock_message(text: str | None, is_bot: bool = False, chat_id: int = 456) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.message_id = 12345
    message.from_user = MagicMock(spec=User)
    message.from_user.id = 123
    message.from_user.is_bot = is_bot
    message.from_user.full_name = "Mock User"
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.answer = AsyncMock()
    message.delete = AsyncMock()
    return message


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
    facebook_command: FacebookCommand,
    text: str | None,
    expected: bool,
    init_container: AsyncContainer,  # noqa: ARG001
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await facebook_command.is_triggered(message) == expected


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_facebook_photo_post(facebook_command: FacebookCommand, init_container: AsyncContainer) -> None:
    """Test handling a Facebook photo post using real extraction and VCR."""
    chat_id = 789
    # Use a real public post for recording
    url = "https://www.facebook.com/share/p/1B37pk4bUf/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    await facebook_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "facebook.com" in caption


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_facebook_video_fallback(
    facebook_command: FacebookCommand, init_container: AsyncContainer
) -> None:
    """Test handling a Facebook video using real extraction and VCR (yt-dlp fallback)."""
    chat_id = 789
    # Use a real public video/reel for recording
    url = "https://www.facebook.com/share/v/1C7Fwittns/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    await facebook_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "facebook.com" in caption
