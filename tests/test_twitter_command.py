"""Tests for the TwitterCommand class."""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User

from hovorunv2.interface.telegram.handlers.twitter import TwitterCommand

if TYPE_CHECKING:
    from hovorunv2.infrastructure.container import Container


@pytest.fixture
def twitter_command() -> TwitterCommand:
    """Fixture to provide a TwitterCommand instance."""
    return TwitterCommand()


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
        ("Check this post: https://twitter.com/user/status/1234567890", True),
        ("Check this post: https://www.twitter.com/user/status/1234567890", True),
        ("Check this post: http://twitter.com/user/status/1234567890", True),
        ("Check this post: https://x.com/user/status/1234567890", True),
        ("Check this post: https://www.x.com/user/status/1234567890", True),
        ("Multiple links: https://x.com/user/status/1 https://twitter.com/user/status/2", True),
        ("Direct API link: https://api.twitter.com/2/tweets/1234567890", True),
        ("No link here", False),
        ("Invalid link: https://twitter.com/user/1234567890", False),
        ("Invalid link: https://facebook.com/user/status/1234567890", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    twitter_command: TwitterCommand,
    text: str | None,
    expected: bool,
    init_container: Container,  # noqa: ARG001
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await twitter_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_is_triggered_bot_user(twitter_command: TwitterCommand) -> None:
    """Test that is_triggered returns False for bot users."""
    message = create_mock_message("https://x.com/user/status/123", is_bot=True)
    assert await twitter_command.is_triggered(message) is False


@pytest.mark.asyncio
async def test_is_triggered_no_user(twitter_command: TwitterCommand) -> None:
    """Test that is_triggered returns False when there is no user context."""
    message = MagicMock(spec=Message)
    message.text = "https://x.com/user/status/123"
    message.from_user = None
    assert await twitter_command.is_triggered(message) is False


@pytest.mark.asyncio
async def test_handle_twitter_post(twitter_command: TwitterCommand, init_container: Container) -> None:
    """Test handling a Twitter post with real service logic and mocked network."""
    chat_id = 456
    url = "https://x.com/elonmusk/status/1234567890"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat first (real DB)
    await init_container.whitelist_service.add_to_whitelist(chat_id)

    # Mock vxtwitter API response
    twitter_response = {
        "text": "Hello world! #twitter",
        "user_name": "Elon Musk",
        "user_screen_name": "elonmusk",
        "retweets": 100,
        "likes": 500,
        "replies": 50,
        "media_extended": [{"url": "https://example.com/image.jpg", "type": "image"}],
    }

    # Mock Translation API response
    translation_response = [[["Hello world!", "Hello world!", None, None, 1]], None, "en"]

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        if "vxtwitter.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=twitter_response)
        elif "translate.googleapis.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=translation_response)
        else:
            mock_resp.status = HTTPStatus.NOT_FOUND
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    with patch("aiohttp.ClientSession.get", side_effect=mocked_get):
        await twitter_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "Elon Musk" in caption
    assert "Hello world!" in caption
    assert "📊" in caption
    assert "🔁 100" in caption
    assert "❤️ 500" in caption
    assert "💬 50" in caption
