"""Validate translation response guards over a real local HTTP boundary."""

from contextlib import AsyncExitStack

import aiohttp
import pytest
from aiohttp import web

from hovorunv2.application.services.translation_providers import GoogleTranslationService
from hovorunv2.application.worker_io import HTTPFailure, HTTPTransportError

RESPONSE_PAYLOAD = web.AppKey("translation_response", object)


async def serve_translation_response(request: web.Request) -> web.Response:
    """Serve a synthetic external translation API response."""
    return web.json_response(request.app[RESPONSE_PAYLOAD])


@pytest.mark.parametrize(
    ("response_payload", "expected_text"),
    [
        ([[["Hello"], [" world"]], None, "en"], "Hello world"),
        ([[[], ["Hello"], None, "invalid", [123], [None], [""]], None, "en"], "Hello"),
        ([[[], None, [123]], None, "en"], None),
        ([[[" "]], None, "en"], None),
        ([[], None, "en"], None),
        (["invalid", None, "en"], None),
        ([None, None, "en"], None),
        ([[]], None),
        ({"invalid": "shape"}, None),
    ],
)
async def test_translation_response_segments(
    response_payload: object,
    expected_text: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep valid segments without indexing empty or joining non-text segments."""
    app = web.Application()
    app[RESPONSE_PAYLOAD] = response_payload
    app.router.add_get("/translate", serve_translation_response)
    runner = web.AppRunner(app)
    await runner.setup()
    async with AsyncExitStack() as resources:
        resources.push_async_callback(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        monkeypatch.setattr(GoogleTranslationService, "API_URL", f"http://127.0.0.1:{port}/translate")
        session = await resources.enter_async_context(aiohttp.ClientSession())
        if expected_text is None:
            with pytest.raises(HTTPTransportError) as error:
                await GoogleTranslationService().translate(session, "synthetic input", "uk")
            assert error.value.code == HTTPFailure.MALFORMED
            return
        result = await GoogleTranslationService().translate(session, "synthetic input", "uk")

        assert result is not None
        assert result.text == expected_text
        assert result.source_lang == "en"
