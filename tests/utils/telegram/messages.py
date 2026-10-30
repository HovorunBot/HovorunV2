"""Telegram mock message helpers for command tests."""

from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Chat, Message, User


def create_mock_message(text: str | None, *, is_bot: bool = False, chat_id: int = 456, user_id: int = 123) -> MagicMock:
    """Create a standardized mock Telegram message for command tests."""
    message = MagicMock(spec=Message)
    message.text = text
    message.message_id = 12345
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.from_user.is_bot = is_bot
    message.sender_chat = None
    message.from_user.full_name = "Mock User"
    message.from_user.username = "mockuser"
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.chat.type = "group"
    message.chat.title = "Mock Group"
    message.answer = AsyncMock()
    message.delete = AsyncMock()
    message.__class__ = Message
    return message


def create_private_media_message(text: str | None, *, chat_id: int = 456, is_bot: bool = False) -> MagicMock:
    """Use a consistent private Telegram transport and matching actor identity."""
    message = create_mock_message(text, chat_id=chat_id, user_id=chat_id, is_bot=is_bot)
    message.chat.type = "private"
    return message
