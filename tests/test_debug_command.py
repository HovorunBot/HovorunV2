"""Tests for the DebugCommand class."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Message, User

from hovorunv2.interface.telegram.handlers.debug import DebugCommand


@pytest.fixture
def debug_command() -> DebugCommand:
    """Fixture to provide a DebugCommand instance."""
    return DebugCommand()


def create_mock_message(text: str | None, user_id: int = 123) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/debug", True),
        ("/debug info", True),
        (" /debug ", True),
        ("/other", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(debug_command: DebugCommand, text: str | None, expected: bool) -> None:
    """Test the is_triggered method."""
    message = create_mock_message(text)
    assert await debug_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle(debug_command: DebugCommand) -> None:
    """Test the handle method."""
    message = create_mock_message("/debug test")
    bot = MagicMock(spec=Bot)

    await debug_command.handle(message, cast("Bot", bot))

    message.answer.assert_called_once_with("It works. Your message: /debug test.")
