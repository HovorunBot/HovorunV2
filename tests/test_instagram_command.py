"""Tests for the InstagramCommand class."""

from http import HTTPStatus
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.interface.telegram.handlers.instagram import InstagramCommand


@pytest.fixture
async def instagram_command(init_container: AsyncContainer) -> InstagramCommand:
    """Fixture to provide an InstagramCommand instance."""
    return await init_container.get(InstagramCommand)


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
        ("Check this post: https://www.instagram.com/p/C-2v0r0nQ1A/", True),
        ("Check this reel: https://www.instagram.com/reels/C-2v0r0nQ1A/", True),
        ("Check this tv: https://www.instagram.com/tv/C-2v0r0nQ1A/", True),
        ("No link here", False),
        ("Invalid link: https://instagram.com/user/", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    instagram_command: InstagramCommand,
    text: str | None,
    expected: bool,
    init_container: AsyncContainer,  # noqa: ARG001
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await instagram_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_instagram_post(instagram_command: InstagramCommand, init_container: AsyncContainer) -> None:
    """Test handling an Instagram post with mocked instaloader and network."""
    chat_id = 789
    url = "https://www.instagram.com/p/C-2v0r0nQ1A/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    # Mock instaloader Post
    mock_post = MagicMock()
    mock_post.owner_username = "test_user"
    mock_post.owner_profile.full_name = "Test Full Name"
    mock_post.caption = "Beautiful sunset #nature"
    mock_post.typename = "GraphImage"
    mock_post.is_video = False
    mock_post.display_url = "https://example.com/sunset.jpg"
    mock_post.likes = 100
    mock_post.comments = 10

    # Mock Translation API response
    translation_response = [[["Beautiful sunset!", "Beautiful sunset!", None, None, 1]], None, "en"]

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        if "translate.googleapis.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=translation_response)
        else:
            mock_resp.status = HTTPStatus.NOT_FOUND
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    session = await init_container.get(aiohttp.ClientSession)
    with (
        patch("instaloader.Post.from_shortcode", return_value=mock_post),
        patch("aiohttp.ClientSession.get", side_effect=mocked_get),
    ):
        await instagram_command.handle(message, cast("Bot", bot), session=session)

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "Test Full Name" in caption
    assert "Beautiful sunset" in caption
    assert "❤️ 100" in caption


@pytest.mark.asyncio
async def test_handle_instagram_reel(instagram_command: InstagramCommand, init_container: AsyncContainer) -> None:
    """Test handling an Instagram reel."""
    chat_id = 789
    url = "https://www.instagram.com/reel/C-2v0r0nQ1A/"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    # Mock instaloader Post
    mock_post = MagicMock()
    mock_post.owner_username = "test_user"
    mock_post.owner_profile.full_name = "Test Full Name"
    mock_post.caption = "Cool reel"
    mock_post.typename = "GraphVideo"
    mock_post.is_video = True
    mock_post.video_url = "https://example.com/reel.mp4"
    mock_post.video_view_count = 5000
    mock_post.likes = 200
    mock_post.comments = 20

    # Mock Translation API response
    translation_response = [[["Cool reel", "Cool reel", None, None, 1]], None, "en"]

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        if "translate.googleapis.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=translation_response)
        else:
            mock_resp.status = HTTPStatus.NOT_FOUND
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    session = await init_container.get(aiohttp.ClientSession)
    with (
        patch("instaloader.Post.from_shortcode", return_value=mock_post),
        patch("aiohttp.ClientSession.get", side_effect=mocked_get),
    ):
        await instagram_command.handle(message, cast("Bot", bot), session=session)

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "👁️ 5K" in caption
