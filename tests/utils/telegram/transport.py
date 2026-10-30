"""External aiohttp response factories and transport constants for Telegram tests."""

import inspect
import re
from collections.abc import Callable
from functools import partial
from types import SimpleNamespace
from typing import Any

from aiohttp import ClientResponse
from aioresponses import CallbackResult, aioresponses
from multidict import CIMultiDict
from yarl import URL

BOT_CREDENTIAL = "123456:synthetic-membership-token"
API = f"https://api.telegram.org/bot{BOT_CREDENTIAL}"
GROUP_ID = -180018
USER_ID = 180018


class RecordedResponse(ClientResponse):
    """Supply aiohttp's new stream_writer argument until aioresponses supports it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, stream_writer=SimpleNamespace(output_size=0), **kwargs)


async def _recorded_callback(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> CallbackResult | None:
    """Apply the response factory to callback results as well as ordinary configured responses."""
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return None
    if not isinstance(result, CallbackResult):
        msg = "External HTTP callbacks require CallbackResult or None."
        raise TypeError(msg)
    if result.response_class is None:
        result.response_class = RecordedResponse
    return result


class RecordedResponses(aioresponses):
    """Configure response construction per request; the parent ignores a context-level response_class."""

    def add(
        self,
        url: str | re.Pattern[str] | URL,
        method: str = "GET",
        status: int = 200,
        body: str | bytes = "",
        exception: Exception | None = None,
        content_type: str = "application/json",
        payload: dict[Any, Any] | None = None,
        headers: CIMultiDict | dict[Any, Any] | None = None,
        response_class: type[ClientResponse] | None = RecordedResponse,
        repeat: bool | int = False,
        timeout: bool = False,
        reason: str | None = None,
        callback: Callable[..., Any] | None = None,
    ) -> None:
        """Provide real aiohttp response objects while replacing external HTTP only."""
        if callback:
            callback = partial(_recorded_callback, callback)
        super().add(
            url,
            method,
            status,
            body,
            exception,
            content_type,
            payload,
            headers,
            response_class,
            repeat,
            timeout,
            reason,
            callback,
        )
