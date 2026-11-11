"""LogContextMiddleware binds user and chat identity onto logging ContextVars and LogRecords."""

import logging
from datetime import UTC, datetime

import pytest
from aiogram.types import Chat, Message, Update, User

from hovorunv2.infrastructure.logger import (
    LogContextFilter,
    current_chat_id,
    current_user_id,
    reset_log_context,
    set_log_context,
)
from hovorunv2.interface.telegram.middlewares import LogContextMiddleware


@pytest.mark.unit
async def test_log_context_middleware_tracks_user_and_chat_id() -> None:
    """The middleware binds user and chat identity to ContextVars and the emitted LogRecord."""
    middleware = LogContextMiddleware()
    log_filter = LogContextFilter()
    tokens = set_log_context(None, None)
    try:
        update = Update(
            update_id=1,
            message=Message(
                message_id=10,
                date=datetime.now(UTC),
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
