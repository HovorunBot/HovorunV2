"""Tests for the ThreadsCommand class."""

from http import HTTPStatus
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.browser import BrowserService
from hovorunv2.interface.telegram.handlers.threads import ThreadsCommand


@pytest.fixture
async def threads_command(init_container: AsyncContainer) -> ThreadsCommand:
    """Fixture to provide a ThreadsCommand instance."""
    return await init_container.get(ThreadsCommand)


# Test Constants
MOCK_CHAT_ID: int = 456
MOCK_USER_ID: int = 123
MOCK_MESSAGE_ID: int = 12345
EXPECTED_MEDIA_COUNT: int = 1


def create_mock_message(text: str | None, is_bot: bool = False, chat_id: int = MOCK_CHAT_ID) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.message_id = MOCK_MESSAGE_ID
    message.from_user = MagicMock(spec=User)
    message.from_user.id = MOCK_USER_ID
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
        ("https://www.threads.net/@zuck/post/CuW6-7Ky5jG", True),
        ("https://www.threads.com/@zuck/post/CuW6-7Ky5jG", True),
        ("https://threads.net/@user/post/123456", True),
        ("https://threads.com/@user/post/123456", True),
        ("http://threads.net/t/C_xyz123", True),
        ("http://threads.com/t/C_xyz123", True),
        ("https://www.threads.net/t/CuW6-7Ky5jG", True),
        ("https://www.threads.com/t/CuW6-7Ky5jG", True),
        ("No link here", False),
        ("Invalid link: https://threads.net/user/12345", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(
    threads_command: ThreadsCommand,
    text: str | None,
    expected: bool,
    init_container: AsyncContainer,  # noqa: ARG001
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await threads_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_threads_post(threads_command: ThreadsCommand, init_container: AsyncContainer) -> None:
    """Test handling a Threads post with real service logic and mocked network."""
    chat_id = MOCK_CHAT_ID
    url = "https://www.threads.com/@zuck/post/CuW6-7Ky5jG"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat first (real DB)
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    # Mock native Threads HTML response
    html_response = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta property="og:title" content="Mark Zuckerberg (@zuck) on Threads"/>
        <meta property="og:description" content="Hello Threads! #threads"/>
        <meta property="og:image" content="https://example.com/image.jpg"/>
        <meta property="og:url" content="https://www.threads.com/@zuck/post/CuW6-7Ky5jG"/>
    </head>
    </html>
    """

    # Mock Translation API response
    translation_response = [[["Hello Threads!", "Hello Threads!", None, None, 1]], None, "en"]

    session = await init_container.get(aiohttp.ClientSession)
    browser_service = await init_container.get(BrowserService)
    with patch("aiohttp.ClientSession.get") as mock_get:
        # Mock Translation API
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        mock_resp.json = AsyncMock(return_value=translation_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        mock_get.return_value = mock_resp

        # Mock BrowserService
        with patch.object(browser_service, "get_content", AsyncMock(return_value=html_response)):
            await threads_command.handle(message, cast("Bot", bot), session=session)

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    assert "Mark Zuckerberg" in kwargs["media"][0].caption
    assert "threads.com" in kwargs["media"][0].caption
    # Verify media items were processed
    assert len(kwargs["media"]) == EXPECTED_MEDIA_COUNT
    assert kwargs["media"][0].media == "https://example.com/image.jpg"
