"""Tests for the SetLanguageCommand class."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.interface.telegram.handlers.set_language import SetLanguageCommand


@pytest.fixture
async def set_language_command(init_container: AsyncContainer) -> SetLanguageCommand:
    """Fixture to provide a SetLanguageCommand instance."""
    return await init_container.get(SetLanguageCommand)


def create_mock_message(text: str | None, user_id: int = 123, chat_id: int = 456) -> MagicMock:
    """Create a mock Telegram message."""
    message = MagicMock(spec=Message)
    message.text = text
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.from_user.is_bot = False
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/set_lang uk", True),
        ("/set_lang uk en,ru", True),
        (" /set_lang uk ", True),
        ("/set_lang", True),  # is_triggered only checks prefix
        ("/other", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(set_language_command: SetLanguageCommand, text: str | None, expected: bool) -> None:
    """Test the is_triggered method."""
    message = create_mock_message(text)
    assert await set_language_command.is_triggered(message) == expected


@pytest.mark.asyncio
async def test_handle_authorized(set_language_command: SetLanguageCommand, init_container: AsyncContainer) -> None:
    """Test handling by an authorized user."""
    admin_id = settings.admin_ids[0]
    chat_id = 789
    message = create_mock_message("/set_lang uk en,ru", user_id=admin_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    await set_language_command.handle(message, cast("Bot", bot))

    # Check database directly
    session_maker = await init_container.get(async_sessionmaker)
    async with session_maker() as session:
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(chat_id, "telegram")
        assert chat is not None
        assert chat.target_lang == "uk"

        ignored_langs = chat.ignored_langs
        assert ignored_langs is not None
        ignored = json.loads(ignored_langs)

        assert "en" in ignored
        assert "ru" in ignored
        assert "und" in ignored

    message.answer.assert_called_once()
    assert "Translation settings updated" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_invalid_format(set_language_command: SetLanguageCommand) -> None:
    """Test handling with invalid format."""
    admin_id = settings.admin_ids[0]
    chat_id = 789
    message = create_mock_message("/set_lang", user_id=admin_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    await set_language_command.handle(message, cast("Bot", bot))

    message.answer.assert_called_once()
    assert "Invalid format" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_unauthorized(set_language_command: SetLanguageCommand, init_container: AsyncContainer) -> None:
    """Test handling by an unauthorized user."""
    user_id = 999  # Not in admin_ids
    chat_id = 7890
    message = create_mock_message("/set_lang uk", user_id=user_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    await set_language_command.handle(message, cast("Bot", bot))

    session_maker = await init_container.get(async_sessionmaker)
    async with session_maker() as session:
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(chat_id, "telegram")
        assert chat is None

    message.answer.assert_not_called()
