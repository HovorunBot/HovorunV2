"""Tests for the BlueskyCommand class."""

from http import HTTPStatus
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.interface.telegram.handlers.bluesky import BlueskyCommand


@pytest.fixture
async def bluesky_command(init_container: AsyncContainer) -> BlueskyCommand:
    """Fixture to provide a BlueskyCommand instance."""
    return await init_container.get(BlueskyCommand)


EXPECTED_MEDIA_COUNT: int = 2


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
    init_container: AsyncContainer,  # noqa: ARG001
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await bluesky_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_bluesky_post(bluesky_command: BlueskyCommand, init_container: AsyncContainer) -> None:
    """Test handling a Bluesky post using public API."""
    chat_id = 789
    url = "https://bsky.app/profile/danabra.mov/post/3lj6v5szvj22a"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

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

    session = await init_container.get(aiohttp.ClientSession)
    with patch("aiohttp.ClientSession.get", side_effect=mocked_get):
        await bluesky_command.handle(message, cast("Bot", bot), session=session)

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Dan" in caption
    assert "Hello Bluesky!" in caption
    assert "❤️" in caption
    assert "Open on Bluesky" in caption
    assert kwargs["media"][0].media == "https://example.com/image.jpg"


@pytest.mark.asyncio
async def test_handle_bluesky_quote_with_media(bluesky_command: BlueskyCommand, init_container: AsyncContainer) -> None:
    """Test handling a Bluesky quote post where the quoted post has media."""
    chat_id = 789
    url = "https://bsky.app/profile/danabra.mov/post/quote123"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    # Mock Thread response with recordWithMedia (Main post media + Quote)
    thread_response = {
        "thread": {
            "post": {
                "record": {"text": "Look at this quote!"},
                "author": {"handle": "danabra.mov", "displayName": "Dan"},
                "likeCount": 10,
                "repostCount": 5,
                "replyCount": 2,
                "embed": {
                    "$type": "app.bsky.embed.recordWithMedia#view",
                    "media": {
                        "$type": "app.bsky.embed.images#view",
                        "images": [{"fullsize": "https://example.com/main.jpg"}],
                    },
                    "record": {
                        "record": {
                            "$type": "app.bsky.feed.post#view",
                            "author": {"handle": "other.bsky.social", "displayName": "Other"},
                            "record": {"text": "Quoted post text"},
                            "embed": {
                                "$type": "app.bsky.embed.images#view",
                                "images": [{"fullsize": "https://example.com/quoted.jpg"}],
                            },
                        }
                    },
                },
            }
        }
    }

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        if "getProfile" in str(url_):
            mock_resp.json = AsyncMock(return_value={"did": "did:plc:123"})
        elif "getPostThread" in str(url_):
            mock_resp.json = AsyncMock(return_value=thread_response)
        else:
            mock_resp.status = HTTPStatus.NOT_FOUND
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    session = await init_container.get(aiohttp.ClientSession)
    with patch("aiohttp.ClientSession.get", side_effect=mocked_get):
        await bluesky_command.handle(message, cast("Bot", bot), session=session)

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Look at this quote!" in caption
    assert "Quoted post text" in caption
    assert "Post includes quoted media" in caption
    assert len(kwargs["media"]) == EXPECTED_MEDIA_COUNT
    assert kwargs["media"][0].media == "https://example.com/main.jpg"
    assert kwargs["media"][1].media == "https://example.com/quoted.jpg"
