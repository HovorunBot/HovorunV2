"""Native translation uses real PostgreSQL preferences and a synthetic external HTTP endpoint."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from dishka import AsyncContainer

from hovorunv2.application.jobs.http_ports import HTTPTransportError
from hovorunv2.application.ports import ChatStore
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.translation.translation_rules import parse_google_translation
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.native_http import NativeHTTPClient
from tests.utils.identities import database_config
from tests.utils.sync_http import HTTPFixture, json_reply
from tests.utils.usage import GROUP, usage_subjects


async def test_native_translation_preserves_defaults_empty_ignores_and_fresh_settings(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Native reads retain unset versus empty preferences and never reuse a stale settings snapshot."""
    await usage_subjects(test_container)
    settings = (await test_container.get(Settings)).model_copy(
        update={"translation_target_lang": "uk", "translation_ignored_langs": ["en", "fr"]}
    )
    chats = await test_container.get(ChatStore)
    response = [[["<synthetic translation>", "synthetic source", None, None]], None, "fr"]
    with (
        HTTPFixture({"/translate": json_reply(response)}) as server,
        NativeHTTPClient() as http,
        WorkerDatabase(database_config(postgres_database_url)) as worker,
    ):
        service = NativeTranslationService(http, settings, worker.policies, api_url=server.url("/translate"))
        assert await asyncio.to_thread(worker.policies.chat_translation_settings, GROUP) == (None, None)
        assert await asyncio.to_thread(service.translate_if_needed, "&lt;synthetic source&gt;", GROUP) is None
        query = parse_qs(urlsplit(server.requests[-1].path).query)
        assert query["tl"] == ["uk"]
        assert query["q"] == ["<synthetic source>"]
        await chats.update_translation_settings(GROUP, "de", [], actor=ActorContext.cli())
        assert await asyncio.to_thread(worker.policies.chat_translation_settings, GROUP) == ("de", ())
        result = await asyncio.to_thread(service.translate_if_needed, "synthetic source", GROUP)
        assert result is not None
        assert result.text == "<synthetic translation>"
        assert result.target_lang == "de"
        assert result.flag == "🇫🇷"
        assert parse_qs(urlsplit(server.requests[-1].path).query)["tl"] == ["de"]
        escaped = await asyncio.to_thread(service.escape_and_translate, "<synthetic source>", GROUP)
        assert escaped.startswith("&lt;synthetic source&gt;")
        assert "&lt;synthetic translation&gt;" in escaped
        await chats.update_translation_settings(GROUP, "de", ["fr"], actor=ActorContext.cli())
        assert await asyncio.to_thread(service.translate_if_needed, "synthetic source", GROUP) is None
        await chats.soft_delete_chat(GROUP, actor=ActorContext.cli(), reason="synthetic")
        assert await asyncio.to_thread(worker.policies.chat_translation_settings, GROUP) is None
        assert await asyncio.to_thread(service.translate_if_needed, "synthetic source", GROUP) is None
        before = len(server.requests)
        assert await asyncio.to_thread(service.translate_if_needed, " ", GROUP) is None
        assert len(server.requests) == before


@pytest.mark.parametrize("response", [None, {}, [], [[], None, "fr"], [[["text"]], None, {}]])
def test_shared_translation_parser_rejects_malformed_external_responses(response: object) -> None:
    """Malformed provider data cannot create a display translation or leak through an exception."""
    with pytest.raises(HTTPTransportError):
        parse_google_translation(response)
