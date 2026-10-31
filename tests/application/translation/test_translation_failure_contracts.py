"""Required translation failure and best-effort caption recovery over real HTTP and PostgreSQL."""

import asyncio
from http import HTTPStatus

import aiohttp
import pytest
from dishka import AsyncContainer

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPTransportError
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.translation.providers import GoogleTranslationService
from hovorunv2.application.translation.service import TranslationService
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.native_http import NativeHTTPClient
from tests.utils.identities import database_config
from tests.utils.sync_http import HTTPFixture, HTTPReply


@pytest.mark.parametrize(
    ("reply", "failure"),
    [
        (HTTPReply(b"invalid-json"), HTTPFailure.MALFORMED),
        (HTTPReply(status=HTTPStatus.SERVICE_UNAVAILABLE), HTTPFailure.REJECTED),
        (HTTPReply(disconnect=True), HTTPFailure.UNAVAILABLE),
    ],
)
async def test_required_provider_failure_does_not_interrupt_caption_translation_policy(
    test_container: AsyncContainer,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    reply: HTTPReply,
    failure: HTTPFailure,
) -> None:
    """Both owning use cases skip optional translation, while direct provider errors stay explicit and sanitized."""
    settings = await test_container.get(Settings)
    with (
        HTTPFixture({"/translate": reply}) as server,
        NativeHTTPClient() as http,
        WorkerDatabase(database_config(postgres_database_url)) as worker,
    ):
        endpoint = server.url("/translate")
        monkeypatch.setattr(GoogleTranslationService, "API_URL", endpoint)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(HTTPTransportError) as error:
                await GoogleTranslationService().translate(session, "synthetic caption", "uk")
            assert error.value.code == failure
            assert str(error.value) == failure.value
        service = await test_container.get(TranslationService)
        assert await service.translate_if_needed("synthetic caption", 101) is None
        assert await service.escape_and_translate("<synthetic caption>", 101) == "&lt;synthetic caption&gt;"
        native = NativeTranslationService(http, settings, worker.policies, api_url=endpoint)
        assert await asyncio.to_thread(native.translate_if_needed, "synthetic caption", 101) is None
        assert (
            await asyncio.to_thread(native.escape_and_translate, "<synthetic caption>", 101)
        ) == "&lt;synthetic caption&gt;"
