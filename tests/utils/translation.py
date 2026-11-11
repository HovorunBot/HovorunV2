"""Compose real local translation with an explicitly unavailable external model source."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.translation.native_translation import NativeTranslationService, TranslationSettingsPort
from hovorunv2.infrastructure.translation.ctranslate2_engine import CTranslate2Engine
from hovorunv2.infrastructure.translation.language_detector import Py3LangIdDetector
from hovorunv2.infrastructure.translation.model_store import ModelHTTPClient, ModelStore
from tests.utils.sync_http import HTTPFixture


@contextmanager
def unavailable_translation(
    settings: AppSettings, preferences: TranslationSettingsPort, server: HTTPFixture
) -> Generator[NativeTranslationService]:
    """Use real detection and inference adapters with loopback model failure instead of external access."""
    with (
        TemporaryDirectory(prefix="hovorun-translation-") as temporary,
        ModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        engine = CTranslate2Engine(ModelStore(Path(temporary) / "model", http, base_url=server.url("/models")))
        try:
            yield NativeTranslationService(Py3LangIdDetector(), engine, settings, preferences)
        finally:
            engine.close()
