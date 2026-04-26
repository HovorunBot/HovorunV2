"""Tests for the BlueskyCommand class."""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User

from hovorunv2.interface.telegram.handlers.bluesky import BlueskyCommand

if TYPE_CHECKING:
    from hovorunv2.infrastructure.container import Container


@pytest.fixture
def bluesky_command() -> BlueskyCommand:
    """Fixture to provide a BlueskyCommand instance."""
    return BlueskyCommand()


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
        ("https://bsky.app/profile/danabra.mov/post/3lj6v5szvj22a", True),
        ("Check this: https://www.bsky.app/profile/user.bsky.social/post/rkey123", True),
        ("No link here", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    bluesky_command: BlueskyCommand,
    text: str | None,
    expected: bool,
    init_container: Container,  # noqa: ARG001
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await bluesky_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_bluesky_post(bluesky_command: BlueskyCommand, init_container: Container) -> None:
    """Test handling a Bluesky post using public API."""
    chat_id = 789
    url = "https://bsky.app/profile/danabra.mov/post/3lj6v5szvj22a"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    await init_container.whitelist_service.add_to_whitelist(chat_id)

    # Mock Profile response
    profile_response = {"did": "did:plc:123", "handle": "danabra.mov", "displayName": "Dan"}

    # Mock Thread response
    thread_response = {
        "thread": {
            "post": {
                "record": {"text": "Hello Bluesky!"},
                "author": {"handle": "danabra.mov", "displayName": "Dan"},
                "likeCount": 10,
                "repostCount": 5,
                "replyCount": 2,
                "embed": {
                    "$type": "app.bsky.embed.images#view",
                    "images": [{"fullsize": "https://example.com/image.jpg"}],
                },
            }
        }
    }

    # Mock Translation API response
    translation_response = [[["Hello Bluesky!", "Hello Bluesky!", None, None, 1]], None, "en"]

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        if "getProfile" in str(url_):
            mock_resp.json = AsyncMock(return_value=profile_response)
        elif "getPostThread" in str(url_):
            mock_resp.json = AsyncMock(return_value=thread_response)
        elif "translate.googleapis.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=translation_response)
        else:
            mock_resp.status = HTTPStatus.NOT_FOUND
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    with patch("aiohttp.ClientSession.get", side_effect=mocked_get):
        await bluesky_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert "Dan" in kwargs["media"][0].caption
    assert "Hello Bluesky!" in kwargs["media"][0].caption
    assert "Original post on Bluesky" in kwargs["media"][0].caption
    assert kwargs["media"][0].media == "https://example.com/image.jpg"
