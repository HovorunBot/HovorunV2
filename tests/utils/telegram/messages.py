"""Real aiogram message helpers for command tests."""

from aiogram.types import Chat, Message


def create_mock_message(
    text: str | None,
    *,
    is_bot: bool = False,
    chat_id: int = 456,
    user_id: int = 123,
    message_id: int = 12345,
    sender_chat: Chat | None = None,
) -> Message:
    """Create a real aiogram Message model for command tests."""
    data: dict[str, object] = {
        "message_id": message_id,
        "date": 1700000000,
        "chat": {
            "id": chat_id,
            "type": "private" if chat_id > 0 else "group",
            "title": "Mock Group" if chat_id < 0 else None,
        },
        "text": text,
    }
    if sender_chat is not None:
        data["sender_chat"] = sender_chat.model_dump(mode="json")
    else:
        data["from"] = {
            "id": user_id,
            "is_bot": is_bot,
            "first_name": "Mock",
            "last_name": "User",
            "username": "mockuser",
        }
    return Message.model_validate(data)


def create_private_media_message(
    text: str | None,
    *,
    chat_id: int = 456,
    is_bot: bool = False,
    message_id: int = 12345,
) -> Message:
    """Use a consistent private Telegram transport and matching actor identity."""
    return create_mock_message(text, chat_id=chat_id, user_id=chat_id, is_bot=is_bot, message_id=message_id)
