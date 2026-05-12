"""Tests for the SetLanguageCommand class."""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, User
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.interface.telegram.callbacks import LangConfigCallback
from hovorunv2.interface.telegram.handlers import set_language
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
    # Ensure isinstance(message, Message) returns True
    message.__class__ = Message
    return message


def create_mock_callback(user_id: int, chat_id: int = 456, message_id: int = 789) -> MagicMock:
    """Create a mock Telegram callback query."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = MagicMock(spec=User)
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock(spec=Chat)
    callback.message.chat.id = chat_id
    callback.message.chat.type = "supergroup"
    callback.message.message_id = message_id
    callback.answer = AsyncMock()
    # Ensure isinstance(callback, CallbackQuery) returns True
    callback.__class__ = CallbackQuery
    return callback


@pytest.mark.asyncio
async def test_lang_callback_resets_ttl(
    set_language_command: SetLanguageCommand, init_container: AsyncContainer
) -> None:
    """Test that clicking a language toggle resets the inactivity TTL."""
    cleanup_service = await init_container.get(CleanupService)

    chat_id = 555
    message_id = 777
    user_id = settings.admin_ids[0]

    callback = create_mock_callback(user_id, chat_id=chat_id, message_id=message_id)
    callback.message.edit_reply_markup = AsyncMock()

    callback_data = LangConfigCallback(action="target", lang_code="uk")

    with patch.object(cleanup_service, "reset_ttl", new_callable=AsyncMock) as mock_reset:
        await set_language_command.handle_callback(callback, callback_data)
        mock_reset.assert_called_once_with(chat_id, message_id, set_language.CONFIG_WINDOW_TTL)


@pytest.mark.asyncio
async def test_lang_message_self_destructs(
    set_language_command: SetLanguageCommand, init_container: AsyncContainer
) -> None:
    """Test that the language config message is deleted when TTL expires."""
    cleanup_service = await init_container.get(CleanupService)

    # Mock bot
    bot = AsyncMock()
    chat_id = 555
    message_id = 777

    # Mock message
    message = create_mock_message("/config_lang", chat_id=chat_id)
    config_msg = MagicMock(spec=Message)
    config_msg.message_id = message_id
    message.answer.return_value = config_msg

    with patch.object(cleanup_service, "register_message", new_callable=AsyncMock) as mock_reg:
        await set_language_command.handle(message, bot)
        mock_reg.assert_called_once_with(bot, chat_id, message_id, set_language.CONFIG_WINDOW_TTL)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/set_lang uk", True),
        ("/config_lang", True),
        ("/set_lang uk en,ru", True),
        (" /set_lang uk ", True),
        ("/set_lang", True),
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
async def test_handle_authorized_manual(
    set_language_command: SetLanguageCommand, init_container: AsyncContainer
) -> None:
    """Test manual /set_lang handling by an authorized user."""
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
async def test_handle_config_lang_interactive(set_language_command: SetLanguageCommand) -> None:
    """Test /config_lang opens interactive keyboard."""
    admin_id = settings.admin_ids[0]
    chat_id = 789
    message = create_mock_message("/config_lang", user_id=admin_id, chat_id=chat_id)
    bot = MagicMock(spec=Bot)

    await set_language_command.handle(message, cast("Bot", bot))

    message.answer.assert_called_once()
    args, _kwargs = message.answer.call_args
    assert "Language Configuration" in args[0]


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
