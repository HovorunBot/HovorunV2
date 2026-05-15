"""Tests for the unified settings menu and on-join verification."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import types

from hovorunv2.application.data.constants import CommandName
from hovorunv2.interface.telegram.callbacks import (
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand


@pytest.fixture
def mock_cleanup_service() -> MagicMock:
    """Create a mock cleanup service."""
    service = MagicMock()
    service.register_window = AsyncMock()
    service.reset_ttl = AsyncMock()
    service.unregister_window = AsyncMock()
    service.replace_singleton_window = AsyncMock()
    return service


@pytest.fixture
def mock_language_service() -> MagicMock:
    """Create a mock language service."""
    service = MagicMock()
    service.get_chat_settings = AsyncMock(return_value=("uk", ["en"]))
    service.update_settings = AsyncMock()
    return service


@pytest.fixture
def mock_command_service() -> MagicMock:
    """Create a mock command service."""
    service = MagicMock()
    service.is_command_allowed = AsyncMock(return_value=True)
    service.enable_command = AsyncMock()
    service.disable_command = AsyncMock()
    return service


@pytest.fixture
def mock_whitelist_service() -> MagicMock:
    """Create a mock whitelist service."""
    service = MagicMock()
    service.is_whitelisted = AsyncMock(return_value=False)
    return service


@pytest.fixture
def mock_access_service() -> MagicMock:
    """Create a mock access service."""
    service = MagicMock()
    service.is_admin = AsyncMock(return_value=False)
    return service


@pytest.mark.asyncio
async def test_bot_join_prompt(mock_whitelist_service: MagicMock) -> None:
    """Test that bot join sends a verification prompt if not whitelisted."""
    handler = BotJoinHandler(mock_whitelist_service)

    event = MagicMock()
    event.new_chat_member.status = "member"
    event.chat.id = 123

    bot = MagicMock()
    bot.send_message = AsyncMock()

    await handler.handle_my_chat_member_update(event, bot)

    bot.send_message.assert_called_once()
    assert "hello" in bot.send_message.call_args[1]["text"].lower()
    assert "reply_markup" in bot.send_message.call_args[1]


@pytest.mark.asyncio
async def test_settings_main_menu(
    mock_language_service: MagicMock,
    mock_command_service: MagicMock,
    mock_access_service: MagicMock,
    mock_cleanup_service: MagicMock,
) -> None:
    """Test that /settings shows the main menu."""
    command = SettingsCommand(
        mock_language_service,
        mock_command_service,
        mock_access_service,
        mock_cleanup_service,
    )

    message = MagicMock(spec=types.Message)
    message.text = "/settings"
    message.chat = MagicMock()
    message.chat.id = 123
    message.from_user = MagicMock()
    message.from_user.id = 456
    message.answer = AsyncMock()

    # Mock return value of answer
    message.answer.return_value = MagicMock()
    message.answer.return_value.chat.id = 123
    message.answer.return_value.message_id = 999

    bot = MagicMock()

    await command.handle(message, bot)

    message.answer.assert_called_once()
    text = message.answer.call_args[0][0]
    assert "Settings" in text

    reply_markup = message.answer.call_args[1]["reply_markup"]
    buttons = [b.text for row in reply_markup.inline_keyboard for b in row]
    assert "🌐 Languages" in buttons
    assert "⚙️ Modules" in buttons


@pytest.mark.asyncio
async def test_settings_language_menu(
    mock_language_service: MagicMock,
    mock_command_service: MagicMock,
    mock_access_service: MagicMock,
    mock_cleanup_service: MagicMock,
) -> None:
    """Test that clicking Languages shows the language menu."""
    command = SettingsCommand(
        mock_language_service,
        mock_command_service,
        mock_access_service,
        mock_cleanup_service,
    )

    callback_data = SettingsCallback(menu=SettingsMenu.LANG)

    query = MagicMock(spec=types.CallbackQuery)
    query.message = MagicMock(spec=types.Message)
    query.message.chat = MagicMock()
    query.message.chat.id = 123
    query.message.message_id = 789
    query.message.edit_text = AsyncMock()

    await command.handle_callback(query, callback_data)

    query.message.edit_text.assert_called_once()
    text = query.message.edit_text.call_args[0][0]
    assert "Language Settings" in text

    reply_markup = query.message.edit_text.call_args[1]["reply_markup"]
    buttons = [b.text for row in reply_markup.inline_keyboard for b in row]
    assert any("Target" in b for b in buttons)
    assert any("Ignored" in b for b in buttons)


@pytest.mark.asyncio
async def test_settings_modules_toggle(
    mock_language_service: MagicMock,
    mock_command_service: MagicMock,
    mock_access_service: MagicMock,
    mock_cleanup_service: MagicMock,
) -> None:
    """Test that toggling a module works in settings."""
    command = SettingsCommand(
        mock_language_service,
        mock_command_service,
        mock_access_service,
        mock_cleanup_service,
    )

    chat_id = 123
    cmd_name = CommandName.TWITTER
    callback_data = SettingsCallback(menu=SettingsMenu.MODULES, action=SettingsAction.TOGGLE, value=cmd_name)

    query = MagicMock(spec=types.CallbackQuery)
    query.message = MagicMock(spec=types.Message)
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.message.message_id = 789
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    # Mock current state as enabled
    mock_command_service.is_command_allowed.return_value = True

    await command.handle_callback(query, callback_data)

    # Verify command was disabled
    mock_command_service.disable_command.assert_called_once_with(chat_id, cmd_name)
    query.answer.assert_called_once()
    assert "disabled" in query.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_settings_language_target_selection(
    mock_language_service: MagicMock,
    mock_command_service: MagicMock,
    mock_access_service: MagicMock,
    mock_cleanup_service: MagicMock,
) -> None:
    """Test that selecting a target language works."""
    command = SettingsCommand(
        mock_language_service,
        mock_command_service,
        mock_access_service,
        mock_cleanup_service,
    )

    chat_id = 123
    lang_code = "pl"
    callback_data = SettingsCallback(menu=SettingsMenu.LANG_TARGET, action=SettingsAction.SELECT, value=lang_code)

    query = MagicMock(spec=types.CallbackQuery)
    query.message = MagicMock(spec=types.Message)
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.message.message_id = 789
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    await command.handle_callback(query, callback_data)

    # Verify settings updated
    mock_language_service.update_settings.assert_called_once()
    args = mock_language_service.update_settings.call_args[0]
    assert args[1] == lang_code  # target_lang
    query.answer.assert_called_once()


@pytest.mark.asyncio
async def test_settings_language_ignored_toggle(
    mock_language_service: MagicMock,
    mock_command_service: MagicMock,
    mock_access_service: MagicMock,
    mock_cleanup_service: MagicMock,
) -> None:
    """Test that toggling an ignored language works."""
    command = SettingsCommand(
        mock_language_service,
        mock_command_service,
        mock_access_service,
        mock_cleanup_service,
    )

    chat_id = 123
    lang_code = "de"
    callback_data = SettingsCallback(menu=SettingsMenu.LANG_IGNORED, action=SettingsAction.TOGGLE, value=lang_code)

    query = MagicMock(spec=types.CallbackQuery)
    query.message = MagicMock(spec=types.Message)
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.message.message_id = 789
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    # Initial state: uk target, en ignored
    mock_language_service.get_chat_settings.return_value = ("uk", ["en"])

    await command.handle_callback(query, callback_data)

    # Verify settings updated with de added
    mock_language_service.update_settings.assert_called_once()
    args = mock_language_service.update_settings.call_args[0]
    updated_ignored = args[2]
    assert lang_code in updated_ignored
    assert "und" in updated_ignored


@pytest.mark.asyncio
async def test_settings_close_button(
    mock_language_service: MagicMock,
    mock_command_service: MagicMock,
    mock_access_service: MagicMock,
    mock_cleanup_service: MagicMock,
) -> None:
    """Test that the close button deletes the message and unregisters cleanup."""
    command = SettingsCommand(
        mock_language_service,
        mock_command_service,
        mock_access_service,
        mock_cleanup_service,
    )

    query = MagicMock(spec=types.CallbackQuery)
    query.data = "config_close"
    query.message = MagicMock(spec=types.Message)
    query.message.chat = MagicMock()
    query.message.chat.id = 123
    query.message.message_id = 789
    query.message.delete = AsyncMock()
    query.answer = AsyncMock()

    await command.handle_close(query)

    query.message.delete.assert_called_once()
    mock_cleanup_service.unregister_window.assert_called_once_with(123, 789)
    query.answer.assert_called_once()
