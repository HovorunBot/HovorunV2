"""Verify Google-first hybrid routing, lazy local fallback, offline switch, and source preservation."""

import asyncio
from http import HTTPStatus
from pathlib import Path
from threading import Event

import pytest
from dishka import AsyncContainer

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.translation.hybrid_engine import HybridTranslationEngine
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
from hovorunv2.infrastructure.translation.google_engine import GoogleTranslationEngine
from hovorunv2.infrastructure.translation.language_detector import Py3LangIdDetector
from hovorunv2.infrastructure.translation.model_store import ModelHTTPClient, ModelStore
from tests.utils.identities import database_config
from tests.utils.sync_http import HTTPFixture, HTTPReply
from tests.utils.usage import GROUP


class _RecordingLocalEngine(TranslationEnginePort):
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._fail = fail

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        *,
        budget: TranslationBudget | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> str:
        del budget, cancel_token
        self.calls.append((text, source_lang, target_lang))
        if self._fail:
            msg = "Local engine unavailable"
            raise TranslationError(msg)
        return f"[local:{target_lang}] {text}"


def _google_html(translated: str) -> HTTPReply:
    body = f'<html><body><div class="result-container">{translated}</div></body></html>'.encode()
    return HTTPReply(body=body, status=HTTPStatus.OK, headers={"Content-Type": "text/html; charset=utf-8"})


async def test_hybrid_translation_gates_ignored_languages_and_prefers_google_without_loading_local(
    test_container: AsyncContainer,
    postgres_database_url: str,
    tmp_path: Path,
) -> None:
    """Skip ignored languages before HTTP and translate via Google without provisioning local weights."""
    settings = await test_container.get(Settings)
    chats = await test_container.get(ChatStore)
    await chats.update_translation_settings(GROUP, "uk", ["en"], actor=ActorContext.cli())

    french_caption = "Bonjour, voici une belle photographie de notre voyage en France."
    english_caption = "This is an English sentence that is ignored by chat preferences."

    with (
        HTTPFixture({"/m": [_google_html("Привіт, ось гарне фото нашої подорожі до Франції.")]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m")) as google_engine,
        ModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as model_http,
        WorkerDatabase(database_config(postgres_database_url)) as worker,
    ):
        local_engine = CTranslate2Engine(ModelStore(tmp_path / "unused-model", model_http, base_url=server.url("")))
        try:
            hybrid = HybridTranslationEngine(google_engine, local_engine, online_enabled=True)
            service = NativeTranslationService(Py3LangIdDetector(), hybrid, settings, worker.policies)

            assert await asyncio.to_thread(service.translate_if_needed, english_caption, GROUP) is None
            assert len(server.requests) == 0

            result = await asyncio.to_thread(service.translate_if_needed, french_caption, GROUP)
            assert result is not None
            assert result.text == "Привіт, ось гарне фото нашої подорожі до Франції."
            assert len(server.requests) == 1
            assert not (tmp_path / "unused-model").exists()
        finally:
            local_engine.close()


async def test_hybrid_translation_falls_back_locally_and_preserves_source_when_both_fail(
    test_container: AsyncContainer,
    postgres_database_url: str,
) -> None:
    """Restart whole document locally on Google failure, and preserve source if both engines fail."""
    settings = await test_container.get(Settings)
    chats = await test_container.get(ChatStore)
    await chats.update_translation_settings(GROUP, "uk", [], actor=ActorContext.cli())

    caption = "Bonjour, voici une belle photographie de notre voyage en France."
    with (
        HTTPFixture({"/m": [HTTPReply(body=b"forbidden", status=HTTPStatus.FORBIDDEN)]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m")) as google_engine,
        WorkerDatabase(database_config(postgres_database_url)) as worker,
    ):
        local_ok = _RecordingLocalEngine(fail=False)
        hybrid_ok = HybridTranslationEngine(google_engine, local_ok, online_enabled=True)
        service_ok = NativeTranslationService(Py3LangIdDetector(), hybrid_ok, settings, worker.policies)

        fallback_result = await asyncio.to_thread(service_ok.translate_if_needed, caption, GROUP)
        assert fallback_result is not None
        assert fallback_result.text == f"[local:uk] {caption}"
        assert local_ok.calls == [(caption, "fr", "uk")]

        local_fail = _RecordingLocalEngine(fail=True)
        hybrid_fail = HybridTranslationEngine(google_engine, local_fail, online_enabled=True)
        service_fail = NativeTranslationService(Py3LangIdDetector(), hybrid_fail, settings, worker.policies)

        assert await asyncio.to_thread(service_fail.escape_and_translate, caption, GROUP) == caption
        explicit_doc = TranslationDocument(text=caption, chat_id=GROUP, intent=TranslationIntent.EXPLICIT)
        with pytest.raises(TranslationError, match="Local engine unavailable"):
            await asyncio.to_thread(service_fail.translate_document, explicit_doc)


def test_hybrid_translation_offline_switch_skips_google_requests() -> None:
    """Route directly to local engine without HTTP requests when online translation is disabled."""
    with (
        HTTPFixture({"/m": [_google_html("Should not be called")]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m")) as google_engine,
    ):
        local_engine = _RecordingLocalEngine(fail=False)
        hybrid = HybridTranslationEngine(google_engine, local_engine, online_enabled=False)
        assert hybrid.translate("Bonjour", "fr", "uk") == "[local:uk] Bonjour"
    assert len(server.requests) == 0
    assert local_engine.calls == [("Bonjour", "fr", "uk")]


@pytest.mark.unit
@pytest.mark.parametrize("online_enabled", [True, False])
def test_hybrid_rejects_expired_deadline_before_either_backend(online_enabled: bool) -> None:
    """Do not start online work or fallback after the original job deadline expires."""
    online = _RecordingLocalEngine()
    local = _RecordingLocalEngine()
    engine = HybridTranslationEngine(online, local, online_enabled=online_enabled)
    with pytest.raises(TranslationError, match="deadline"):
        engine.translate("Bonjour", "fr", "uk", budget=TranslationBudget(deadline_monotonic=0))
    assert online.calls == []
    assert local.calls == []


class _EventCancellation(CancellationToken):
    def __init__(self, event: Event) -> None:
        self._event = event

    def is_cancelled(self) -> bool:
        return self._event.is_set()


@pytest.mark.unit
def test_hybrid_does_not_fall_back_after_cancellation_during_http() -> None:
    """Stop the whole operation when cancellation arrives after the provider request starts."""
    cancelled = Event()
    reply = HTTPReply(body=b'<div class="result-container">Result</div>', started=cancelled)
    local = _RecordingLocalEngine()
    with (
        HTTPFixture({"/m": [reply]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m")) as google,
    ):
        engine = HybridTranslationEngine(google, local)
        with pytest.raises(TranslationError, match="cancelled"):
            engine.translate("Bonjour", "fr", "uk", cancel_token=_EventCancellation(cancelled))
        assert len(server.requests) == 1
    assert local.calls == []
