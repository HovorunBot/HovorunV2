"""Tests for the DebugCommand class and logging context tracking."""

import logging

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
async def debug_command(test_container: AsyncContainer) -> DebugCommand:
    """Fixture to provide a DebugCommand instance."""
    return await test_container.get(DebugCommand)


def create_message(text: str | None, user_id: int = 123) -> Message:
    """Build a real synthetic Telegram message."""
    return Message(
        message_id=1,
        date=1,
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


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
    message = create_message(text)
    assert await debug_command.is_triggered(message) == expected


async def test_handle(debug_command: DebugCommand, recording_bot: Bot) -> None:
    """Test the handle method."""
    message = create_message("/debug test").as_(recording_bot)

    await debug_command.handle(message, recording_bot)

    assert len(recording_bot.session.calls) == 1
    assert recording_bot.session.calls[0].text == "It works. Your message: /debug test."


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
                date=123456789,
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
