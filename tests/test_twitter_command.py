"""Tests for Twitter rich media command."""

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
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command


@pytest.fixture
async def twitter_command(init_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Twitter RichMediaCommand instance."""
    return await get_media_command(init_container, CommandName.TWITTER)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Check this post: https://twitter.com/elonmusk/status/1234567890", True),
        ("Check this post: https://x.com/elonmusk/status/1234567890", True),
        ("No link here", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    twitter_command: RichMediaCommand,
    text: str | None,
    expected: bool,
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_private_media_message(text)
    assert await twitter_command.is_triggered(message) == expected


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_twitter_post(twitter_command: RichMediaCommand, init_container: AsyncContainer) -> None:
    """Test handling a real Twitter post using VCR."""
    chat_id = 456
    url = "https://x.com/elonmusk/status/2053586034743705773"
    message = create_private_media_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(init_container, chat_id)

    await twitter_command.handle(message, cast("Bot", bot))

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    caption = args[0] if args else kwargs["text"]
    assert "Elon Musk" in caption
    assert "❤️" in caption
    assert "🔁" in caption


@pytest.mark.asyncio
async def test_twitter_split_video_vs_post_toggles(
    twitter_command: RichMediaCommand, init_container: AsyncContainer
) -> None:
    """Test that disabling twitter_post skips text/image tweets while keeping video tweets enabled."""
    chat_id = 789
    text_tweet_url = "https://x.com/user/status/1111111111"
    video_tweet_url = "https://x.com/user/status/2222222222"

    chat_status_service = await init_container.get(ChatStatusService)
    command_service = await init_container.get(CommandService)
    await chat_status_service.approve(chat_id)
    await authorize_private_media(init_container, chat_id)
    await command_service.enable_command(chat_id, CommandName.TWITTER)
    await command_service.disable_command(chat_id, CommandName.TWITTER_POST)

    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    text_tweet_json = {
        "text": "Just a text and image tweet",
        "user_name": "Test User",
        "user_screen_name": "testuser",
        "retweets": 10,
        "likes": 50,
        "replies": 2,
        "media_extended": [{"url": "https://pbs.twimg.com/media/img1.jpg", "type": "image"}],
    }
    video_tweet_json = {
        "text": "Video tweet",
        "user_name": "Test User",
        "user_screen_name": "testuser",
        "retweets": 20,
        "likes": 100,
        "replies": 5,
        "media_extended": [{"url": "https://video.twimg.com/vid1.mp4", "type": "video"}],
    }

    @asynccontextmanager
    async def mock_get(url: str, **_kwargs: Any) -> AsyncGenerator[MagicMock]:
        resp = MagicMock()
        resp.status = HTTPStatus.OK
        if "1111111111" in url:
            resp.json = AsyncMock(return_value=text_tweet_json)
        elif "2222222222" in url:
            resp.json = AsyncMock(return_value=video_tweet_json)
        else:
            resp.json = AsyncMock(return_value=[[[""]], None, "en"])
        yield resp

    msg_post = create_private_media_message(text_tweet_url, chat_id=chat_id)
    with patch.object(aiohttp.ClientSession, "get", side_effect=mock_get):
        await twitter_command.handle(msg_post, cast("Bot", bot))

    bot.send_media_group.assert_not_called()
    msg_post.answer.assert_not_called()

    msg_video = create_private_media_message(video_tweet_url, chat_id=chat_id)
    with patch.object(aiohttp.ClientSession, "get", side_effect=mock_get):
        await twitter_command.handle(msg_video, cast("Bot", bot))

    bot.send_media_group.assert_called_once()
