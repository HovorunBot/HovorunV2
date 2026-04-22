"""Tests for the TikTokCommand class."""

from unittest.mock import MagicMock

import pytest
from aiogram.types import Chat, Message, User

from hovorunv2.interface.telegram.commands.tiktok import TikTokCommand


@pytest.fixture
def tiktok_command() -> TikTokCommand:
    """Fixture to provide a TikTokCommand instance."""
    return TikTokCommand()


def create_mock_message(text: str | None, is_bot: bool = False) -> Message:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.id = 123
    message.from_user.is_bot = is_bot
    message.chat = MagicMock(spec=Chat)
    message.chat.id = 456
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.tiktok.com/@fueltothe_max/video/7397394517311212818", True),
        ("Check this: https://vm.tiktok.com/ZMrBy9vGj/ cool right?", True),
        ("https://tiktok.com/@user/video/1234567890", True),
        ("Just a random text", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(tiktok_command: TikTokCommand, text: str | None, expected: bool) -> None:  # noqa: FBT001
    """Test the is_triggered method."""
    message = create_mock_message(text)
    assert await tiktok_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_is_triggered_by_bot(tiktok_command: TikTokCommand) -> None:
    """Test that it's not triggered by bot messages."""
    text = "https://www.tiktok.com/@fueltothe_max/video/7397394517311212818"
    message = create_mock_message(text, is_bot=True)
    assert await tiktok_command.is_triggered(message) is False
