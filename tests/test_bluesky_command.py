"""Tests for the BlueskyCommand class."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

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
    init_container: AsyncContainer,
) -> None:
    """Test the is_triggered method with various inputs."""
    message = create_mock_message(text)
    assert await bluesky_command.is_triggered(message) == expected


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_bluesky_post(bluesky_command: BlueskyCommand, init_container: AsyncContainer) -> None:
    """Test handling a Bluesky post using real extraction and VCR."""
    chat_id = 789
    # Use a real public post for recording
    url = "https://bsky.app/profile/josiahcohen.bsky.social/post/3mlmib6xdbc2a"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    await bluesky_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "bsky.app" in caption


@pytest.mark.asyncio
@pytest.mark.vcr
async def test_handle_bluesky_quote_with_media(bluesky_command: BlueskyCommand, init_container: AsyncContainer) -> None:
    """Test handling a Bluesky quote post with media using real extraction and VCR."""
    chat_id = 789
    # Use a real public quote post for recording
    url = "https://bsky.app/profile/hikkifox.bsky.social/post/3mlnxm3on6s2x"
    message = create_mock_message(url, chat_id=chat_id)
    bot = MagicMock(spec=Bot)
    bot.send_media_group = AsyncMock()

    # Whitelist the chat
    whitelist_service = await init_container.get(WhitelistService)
    await whitelist_service.add_to_whitelist(chat_id)

    await bluesky_command.handle(message, cast("Bot", bot))

    # Verify interaction
    bot.send_media_group.assert_called_once()
    _args, kwargs = bot.send_media_group.call_args
    caption = kwargs["media"][0].caption
    assert "Mock User" in caption
    assert "bsky.app" in caption
