"""Tests for the AllowBotCommand class."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User

from hovorunv2.infrastructure.config import settings
from hovorunv2.interface.telegram.commands.whitelist import AllowBotCommand

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def mock_container() -> Iterator[MagicMock]:
    """Fixture to mock the container and its services."""
    with patch("hovorunv2.interface.telegram.commands.whitelist.container") as mock:
        mock.whitelist_service = MagicMock()
        mock.whitelist_service.add_to_whitelist = AsyncMock()
        yield mock


@pytest.fixture
def whitelist_command() -> AllowBotCommand:
    """Fixture to provide an AllowBotCommand instance."""
    return AllowBotCommand()


def create_mock_message(text: str | None, user_id: int = 123, chat_id: int = 456) -> Message:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/allow_bot", True),
        (" /allow_bot ", True),
        ("/allow_bot extra", False),
        ("/other_command", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(whitelist_command: AllowBotCommand, text: str | None, expected: bool) -> None:  # noqa: FBT001
    """Test the is_triggered method."""
    message = create_mock_message(text)
    assert await whitelist_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_authorized(whitelist_command: AllowBotCommand, mock_container: MagicMock) -> None:
    """Test handling by an authorized user."""
    admin_id = settings.admin_ids[0]
    chat_id = 789
    message = create_mock_message("/allow_bot", user_id=admin_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    await whitelist_command.handle(message, bot)

    mock_container.whitelist_service.add_to_whitelist.assert_called_once_with(chat_id)
    message.answer.assert_called_once_with("Bot is now allowed in this chat.")  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_handle_unauthorized(whitelist_command: AllowBotCommand, mock_container: MagicMock) -> None:
    """Test handling by an unauthorized user."""
    user_id = 999  # Not in admin_ids
    chat_id = 789
    message = create_mock_message("/allow_bot", user_id=user_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    await whitelist_command.handle(message, bot)

    mock_container.whitelist_service.add_to_whitelist.assert_not_called()
    message.answer.assert_not_called()  # ty: ignore[unresolved-attribute]
