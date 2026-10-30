"""External aiohttp response factories compatible with the installed client constructor."""

import inspect
import re
from collections.abc import Callable
from functools import partial
from typing import Any

from aioresponses import CallbackResult, aioresponses
from yarl import URL

from tests.test_membership_adapter import RecordedResponse


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

    def add(self, url: str | re.Pattern[str] | URL, method: str = "GET", **kwargs: Any) -> None:
        """Provide real aiohttp response objects while replacing external HTTP only."""
        kwargs.setdefault("response_class", RecordedResponse)
        if callback := kwargs.get("callback"):
            kwargs["callback"] = partial(_recorded_callback, callback)
        super().add(url, method=method, **kwargs)
