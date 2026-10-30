"""Recording Telegram transport with real aiogram request and response models."""

import json
from collections.abc import AsyncGenerator
from http import HTTPStatus
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType


class RecordingSession(BaseSession):
    """Record outgoing Telegram methods and parse synthetic API responses."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.responses: list[dict[str, object] | Exception] = []

    async def close(self) -> None:
        """No network resources are opened."""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> TelegramType:
        """Parse recorded external responses through aiogram's response validator."""
        self.calls.append(method)
        if self.responses:
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
        else:
            result = {
                "message_id": 1,
                "date": 1,
                "chat": {"id": getattr(method, "chat_id", 1), "type": "private"},
            }
        response = self.check_response(bot, method, HTTPStatus.OK, json.dumps({"ok": True, "result": result}))
        return response.result

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        """Reject unexpected downloads from the Telegram transport."""
        msg = "RecordingSession does not provide file downloads"
        raise ValueError(msg)
        yield b""
