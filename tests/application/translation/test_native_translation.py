"""Verify local caption translation with fresh real PostgreSQL preferences."""

import asyncio
import os
from pathlib import Path

import pytest
from dishka import AsyncContainer

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.translation.ctranslate2_engine import CTranslate2Engine
from hovorunv2.infrastructure.translation.language_detector import Py3LangIdDetector
from hovorunv2.infrastructure.translation.model_store import ModelHTTPClient, ModelStore
from tests.utils.identities import database_config
from tests.utils.sync_http import HTTPFixture
from tests.utils.translation import unavailable_translation
from tests.utils.usage import GROUP, usage_subjects

CAPTION = "Bonjour, voici une photographie de notre voyage en France."


async def test_native_translation_preserves_defaults_empty_ignores_and_fresh_settings(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Persisted changes affect the next caption and empty ignores override global defaults."""
    directory = os.environ.get("HOVORUN_TRANSLATION_TEST_MODEL_DIR")
    if directory is None:
        pytest.skip("Set HOVORUN_TRANSLATION_TEST_MODEL_DIR to run qualified model inference")
    await usage_subjects(test_container)
    settings = (await test_container.get(Settings)).model_copy(
        update={"translation_target_lang": "uk", "translation_ignored_langs": ["en", "fr"]}
    )
    chats = await test_container.get(ChatStore)
    with ModelHTTPClient() as http, WorkerDatabase(database_config(postgres_database_url)) as worker:
        engine = CTranslate2Engine(ModelStore(Path(directory), http))
        service = NativeTranslationService(Py3LangIdDetector(), engine, settings, worker.policies)
        try:
            assert await asyncio.to_thread(worker.policies.chat_translation_settings, GROUP) == (None, None)
            assert await asyncio.to_thread(service.translate_if_needed, CAPTION, GROUP) is None
            await chats.update_translation_settings(GROUP, "de", [], actor=ActorContext.cli())
            assert await asyncio.to_thread(worker.policies.chat_translation_settings, GROUP) == ("de", ())
            result = await asyncio.to_thread(service.translate_if_needed, CAPTION, GROUP)
            assert result is not None
            assert result.text.strip()
            assert result.text != CAPTION
            assert result.target_lang == "de"
            assert result.flag == "🇫🇷"
            escaped = await asyncio.to_thread(service.escape_and_translate, "<script>" + CAPTION, GROUP)
            assert escaped.startswith("&lt;script&gt;")
            assert "<script>" not in escaped
            assert "<b>Translated:</b>" in escaped
            await chats.update_translation_settings(GROUP, "de", ["fr"], actor=ActorContext.cli())
            assert await asyncio.to_thread(service.translate_if_needed, CAPTION, GROUP) is None
            await chats.soft_delete_chat(GROUP, actor=ActorContext.cli(), reason="synthetic")
            assert await asyncio.to_thread(worker.policies.chat_translation_settings, GROUP) is None
            assert await asyncio.to_thread(service.translate_if_needed, CAPTION, GROUP) is None
        finally:
            engine.close()


@pytest.mark.parametrize(
    "caption",
    [
        "This is an English caption about nature and travel.",
        "Природа України дуже красива.",
        "😃 https://example.org #bonjour @person",
        "a b",
        "",
    ],
)
async def test_ignored_caption_never_reaches_translation_provider(
    test_container: AsyncContainer, postgres_database_url: str, caption: str
) -> None:
    """Ignored, target, and insufficient-evidence captions never request a model artifact."""
    settings = (await test_container.get(Settings)).model_copy(
        update={"translation_target_lang": "uk", "translation_ignored_langs": ["en"]}
    )
    with HTTPFixture({}) as server, WorkerDatabase(database_config(postgres_database_url)) as worker:
        with unavailable_translation(settings, worker.policies, server) as service:
            assert await asyncio.to_thread(service.translate_if_needed, caption, GROUP) is None
        assert len(server.requests) == 0


async def test_unavailable_local_model_keeps_original_caption_deliverable(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Recover from model failure without sending captions outside the process or losing the original."""
    settings = await test_container.get(Settings)
    with HTTPFixture({}) as server, WorkerDatabase(database_config(postgres_database_url)) as worker:
        with unavailable_translation(settings, worker.policies, server) as service:
            result = await asyncio.to_thread(service.escape_and_translate, CAPTION, GROUP)
        assert result == CAPTION
        assert server.requests
        assert all(request.body == b"" and CAPTION not in request.path for request in server.requests)
