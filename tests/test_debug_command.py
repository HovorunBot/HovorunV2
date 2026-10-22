"""Tests for the DebugCommand class and logging context tracking."""

import logging
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update, User
from dishka import AsyncContainer

from hovorunv2.infrastructure.logger import (
    LogContextFilter,
    current_chat_id,
    current_user_id,
    reset_log_context,
    set_log_context,
)
from hovorunv2.interface.telegram.handlers.debug import DebugCommand
from hovorunv2.interface.telegram.middlewares import LogContextMiddleware


@pytest.fixture
async def debug_command(init_container: AsyncContainer) -> DebugCommand:
    """Fixture to provide a DebugCommand instance."""
    return await init_container.get(DebugCommand)


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


@pytest.mark.asyncio
async def test_log_context_middleware_tracks_user_and_chat_id() -> None:
    """Test that LogContextMiddleware binds user_id and chat_id to ContextVars and LogRecord."""
    middleware = LogContextMiddleware()
    log_filter = LogContextFilter()
    tokens = set_log_context(None, None)
    try:
        update = Update(
            update_id=1,
            message=Message(
                message_id=10,
                date=datetime.fromtimestamp(123456789, tz=UTC),
                chat=Chat(id=54321, type="group", title="Test"),
                from_user=User(id=98765, is_bot=False, first_name="Test"),
                text="unhandled text",
            ),
        )

        async def dummy_handler(event: object, data: dict[str, object]) -> str:
            assert current_user_id.get() == 98765
            assert current_chat_id.get() == 54321
            return "ok"

        result = await middleware(dummy_handler, update, {})

        assert result == "ok"
        # Context remains bound after middleware returns so Dispatcher.feed_update finally-block logs have user_id
        record = logging.LogRecord(
            name="aiogram.event",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Update id=1 is not handled.",
            args=(),
            exc_info=None,
        )
        log_filter.filter(record)
        assert getattr(record, "user_id", None) == "98765"
        assert getattr(record, "chat_id", None) == "54321"
    finally:
        reset_log_context(tokens)
