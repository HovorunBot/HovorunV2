"""Tests for the TwitterCommand class."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.data.constants import ChatStatus
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.interface.telegram.handlers.twitter import TwitterCommand


@pytest.fixture
async def twitter_command(init_container: AsyncContainer) -> TwitterCommand:
    """Fixture to provide a TwitterCommand instance."""
    return await init_container.get(TwitterCommand)


# Test Constants
MOCK_CHAT_ID: int = 456
MOCK_USER_ID: int = 123
MOCK_MESSAGE_ID: int = 12345


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
    # Ensure isinstance(message, Message) returns True for Bot
    message.__class__ = Message
    return message


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
    twitter_command: TwitterCommand,
    text: str | None,
    expected: bool,
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await twitter_command.is_triggered(message) == expected


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_twitter_post(twitter_command: TwitterCommand, init_container: AsyncContainer) -> None:
    """Test handling a real Twitter post using VCR."""
    chat_id = MOCK_CHAT_ID
    # Use a real public tweet for recording
    url = "https://x.com/elonmusk/status/2053586034743705773"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Access the chat first (real DB)
    chat_status_service = await init_container.get(ChatStatusService)
    await chat_status_service.set_status(chat_id, ChatStatus.APPROVED)

    await twitter_command.handle(message, cast("Bot", bot))

    # Verify interaction
    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    caption = args[0] if args else kwargs["text"]
    assert "Elon Musk" in caption
    assert "❤️" in caption
    assert "🔁" in caption
