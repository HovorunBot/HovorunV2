"""Verify local caption translation with fresh real PostgreSQL preferences."""

import asyncio
import os
from pathlib import Path

import pytest
from dishka import AsyncContainer

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.translation.ports import (
    CancellationToken,
    TranslationBudget,
    TranslationDocument,
    TranslationEnginePort,
    TranslationError,
    TranslationIntent,
)
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


async def test_incomplete_local_translation_keeps_the_entire_original_caption(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Keep all source content when native inference reaches its decoding limit."""
    directory = os.environ.get("HOVORUN_TRANSLATION_TEST_MODEL_DIR")
    if directory is None:
        pytest.skip("Set HOVORUN_TRANSLATION_TEST_MODEL_DIR to run qualified model inference")
    caption = " ".join(
        f"Le voyage numéro {index} commence demain et nous allons visiter la montagne." for index in range(1, 30)
    )
    settings = await test_container.get(Settings)
    with ModelHTTPClient() as http, WorkerDatabase(database_config(postgres_database_url)) as worker:
        engine = CTranslate2Engine(ModelStore(Path(directory), http))
        service = NativeTranslationService(Py3LangIdDetector(), engine, settings, worker.policies)
        try:
            assert await asyncio.to_thread(service.escape_and_translate, caption, GROUP) == caption
        finally:
            engine.close()


class _StubTranslationEngine(TranslationEnginePort):
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._fail = fail

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        self.calls.append((text, source_lang, target_lang))
        if self._fail:
            msg = "Engine unavailable"
            raise TranslationError(msg)
        return f"[{target_lang}] {text}"


class _CancelledToken(CancellationToken):
    def is_cancelled(self) -> bool:
        return True


async def test_explicit_translation_bypasses_ignored_languages_without_mutating_chat_settings(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Bypass ignored_languages and apply per-request target override for explicit requests only."""
    settings = await test_container.get(Settings)
    chats = await test_container.get(ChatStore)
    await chats.update_translation_settings(GROUP, "uk", ["fr"], actor=ActorContext.cli())

    engine = _StubTranslationEngine()
    with WorkerDatabase(database_config(postgres_database_url)) as worker:
        service = NativeTranslationService(Py3LangIdDetector(), engine, settings, worker.policies)
        auto_result = await asyncio.to_thread(service.translate_if_needed, CAPTION, GROUP)
        assert auto_result is None
        assert engine.calls == []

        explicit_doc = TranslationDocument(
            text=CAPTION,
            chat_id=GROUP,
            intent=TranslationIntent.EXPLICIT,
            target_lang="de",
        )
        explicit_result = await asyncio.to_thread(service.translate_document, explicit_doc)
        assert explicit_result is not None
        assert explicit_result.target_lang == "de"
        assert explicit_result.text == f"[de] {CAPTION}"
        assert engine.calls == [(CAPTION, "fr", "de")]
        persisted = await asyncio.to_thread(worker.policies.chat_translation_settings, GROUP)
        assert persisted == ("uk", ("fr",))


async def test_explicit_translation_raises_on_engine_failure_cancellation_or_expired_budget(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Propagate TranslationError for explicit requests while keeping automatic captions resilient."""
    settings = await test_container.get(Settings)
    failing_engine = _StubTranslationEngine(fail=True)
    with WorkerDatabase(database_config(postgres_database_url)) as worker:
        service = NativeTranslationService(Py3LangIdDetector(), failing_engine, settings, worker.policies)
        assert await asyncio.to_thread(service.translate_if_needed, CAPTION, GROUP) is None

        explicit_doc = TranslationDocument(text=CAPTION, chat_id=GROUP, intent=TranslationIntent.EXPLICIT)
        with pytest.raises(TranslationError, match="Engine unavailable"):
            await asyncio.to_thread(service.translate_document, explicit_doc)

        with pytest.raises(TranslationError, match="cancelled"):
            await asyncio.to_thread(service.translate_document, explicit_doc, cancel_token=_CancelledToken())

        expired_doc = TranslationDocument(
            text=CAPTION,
            chat_id=GROUP,
            intent=TranslationIntent.EXPLICIT,
            budget=TranslationBudget(deadline_monotonic=0.0),
        )
        with pytest.raises(TranslationError, match="deadline"):
            await asyncio.to_thread(service.translate_document, expired_doc)
