"""External HTTP fixture responses exercise their configured JSON instead of constructor failure."""

from http import HTTPStatus

import aiohttp
import pytest
from aioresponses import CallbackResult

from tests.utils.http import RecordedResponses


def callback_response(url: object, **kwargs: object) -> CallbackResult:
    """Supply external data without specifying a per-callback aiohttp response class."""
    return CallbackResult(payload={"ok": True})


def default_callback_response(url: object, **kwargs: object) -> None:
    """Retain the configured request response when an external callback only records effects."""


@pytest.mark.parametrize("mode", ["ordinary", "callback", "default_callback"])
async def test_external_response_factory_returns_actual_configured_json(mode: str) -> None:
    """Ordinary and callback responses honor aiohttp's required response-constructor arguments."""
    with RecordedResponses() as http:
        callback = {"callback": callback_response, "default_callback": default_callback_response}.get(mode)
        http.get("https://synthetic.invalid/endpoint", payload={"ok": True}, callback=callback)
        async with (
            aiohttp.ClientSession() as session,
            session.get("https://synthetic.invalid/endpoint") as response,
        ):
            assert response.status == HTTPStatus.OK
            assert await response.json() == {"ok": True}
