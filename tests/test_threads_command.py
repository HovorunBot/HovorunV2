"""Tests for the ThreadsCommand class."""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User

from hovorunv2.interface.telegram.commands.threads import ThreadsCommand

if TYPE_CHECKING:
    from hovorunv2.infrastructure.container import Container


@pytest.fixture
def threads_command() -> ThreadsCommand:
    """Fixture to provide a ThreadsCommand instance."""
    return ThreadsCommand()


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
        ("Check this post: https://www.threads.net/@zuck/post/CuW6-7Ky5jG", True),
        ("Check this post: https://www.threads.com/@zuck/post/CuW6-7Ky5jG", True),
        ("Check this post: https://threads.net/@user/post/123456", True),
        ("Check this post: https://threads.com/@user/post/123456", True),
        ("Check this post: http://threads.net/t/C_xyz123", True),
        ("Check this post: http://threads.com/t/C_xyz123", True),
        ("Check this post: https://www.threads.net/t/CuW6-7Ky5jG", True),
        ("Check this post: https://www.threads.com/t/CuW6-7Ky5jG", True),
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
    init_container: Container,  # noqa: ARG001
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await threads_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_threads_post(threads_command: ThreadsCommand, init_container: Container) -> None:
    """Test handling a Threads post with real service logic and mocked network."""
    chat_id = 456
    url = "https://www.threads.com/@zuck/post/CuW6-7Ky5jG"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat first (real DB)
    await init_container.whitelist_service.add_to_whitelist(chat_id)

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

    def mocked_get(url_: str, **_kwargs: Any) -> MagicMock:  # noqa: ANN401
        mock_resp = MagicMock()
        mock_resp.status = HTTPStatus.OK
        if "threads.com" in str(url_) or "threads.net" in str(url_):
            mock_resp.text = AsyncMock(return_value=html_response)
        elif "translate.googleapis.com" in str(url_):
            mock_resp.json = AsyncMock(return_value=translation_response)
        else:
            mock_resp.status = HTTPStatus.NOT_FOUND
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    with patch("aiohttp.ClientSession.get", side_effect=mocked_get):
        await threads_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    assert kwargs["chat_id"] == chat_id
    assert "Mark Zuckerberg" in kwargs["media"][0].caption
    assert "threads.com" in kwargs["media"][0].caption
    # Verify media items were processed
    assert len(kwargs["media"]) == 1
    assert kwargs["media"][0].media == "https://example.com/image.jpg"
