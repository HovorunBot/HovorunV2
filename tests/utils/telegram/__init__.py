"""Structured Telegram test utilities."""

from tests.utils.telegram.events import bot_event, member_payload
from tests.utils.telegram.messages import create_mock_message, create_private_media_message
from tests.utils.telegram.transport import (
    API,
    BOT_CREDENTIAL,
    GROUP_ID,
    USER_ID,
    RecordedResponse,
    RecordedResponses,
)

__all__ = [
    "API",
    "BOT_CREDENTIAL",
    "GROUP_ID",
    "USER_ID",
    "RecordedResponse",
    "RecordedResponses",
    "bot_event",
    "create_mock_message",
    "create_private_media_message",
    "member_payload",
]
