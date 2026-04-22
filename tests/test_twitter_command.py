"""Tests for the TwitterCommand class."""

from unittest.mock import MagicMock

import pytest
from aiogram.types import Message, User

from hovorunv2.interface.telegram.commands.twitter import TwitterCommand


@pytest.fixture
def twitter_command() -> TwitterCommand:
    """Fixture to provide a TwitterCommand instance."""
    return TwitterCommand()


def create_mock_message(text: str | None, is_bot: bool = False) -> Message:  # noqa: FBT001, FBT002
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.is_bot = is_bot
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
async def test_is_triggered(twitter_command: TwitterCommand, text: str | None, expected: bool) -> None:  # noqa: FBT001
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
