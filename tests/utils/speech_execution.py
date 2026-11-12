"""Compose genuine speech execution resources with synthetic external Telegram HTTP."""

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from hovorunv2.application.speech.runner import SpeechJobRunner
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import WorkerRuntime
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.speech.audio_http import SpeechAudioHTTPClient
from hovorunv2.infrastructure.speech.model_store import SpeechModelHTTPClient, SpeechModelStore
from hovorunv2.infrastructure.speech.whisper_engine import WhisperCppEngine
from hovorunv2.interface.telegram.transport.speech import NativeSpeechTransport
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI
from tests.utils.runtime import ManualClock
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply
from tests.utils.telegram import BOT_CREDENTIAL


@dataclass
class SpeechExecution:
    """Expose real persisted job results and captured synthetic transport effects."""

    runner: SpeechJobRunner
    runtime: WorkerRuntime
    database: WorkerDatabase
    server: HTTPFixture


def qualified_model_directory() -> Path:
    """Require an explicitly provisioned model for genuine local inference tests.

    Returns:
        Directory containing the qualified multilingual model.
    """
    value = os.environ.get("HOVORUN_SPEECH_TEST_MODEL_DIR")
    if value is None:
        pytest.skip("Set HOVORUN_SPEECH_TEST_MODEL_DIR for native-model integration tests")
    return Path(value)


@contextmanager
def speech_execution(
    settings: Settings,
    namespace: RuntimeNamespace,
    clock: ManualClock,
    audio: bytes,
    *,
    reply: HTTPReply | None = None,
    audio_reply: HTTPReply | None = None,
    missing_model: Path | None = None,
) -> Generator[SpeechExecution]:
    """Run real stores, policy, inference and transport; replace only external HTTP endpoints."""
    model_dir = missing_model if missing_model is not None else qualified_model_directory()
    helper_path = Path(
        os.environ.get("HOVORUN_SPEECH_TEST_HELPER", "src/hovorunv2/infrastructure/speech/whisper_helper")
    )
    routes = {
        f"/bot{BOT_CREDENTIAL}/getFile": json_reply(
            {
                "ok": True,
                "result": {"file_id": "synthetic", "file_path": "voice/synthetic.ogg", "file_size": len(audio)},
            }
        ),
        f"/file/bot{BOT_CREDENTIAL}/voice/synthetic.ogg": audio_reply or HTTPReply(body=audio),
        f"/bot{BOT_CREDENTIAL}/sendMessage": reply or json_reply({"ok": True, "result": {"message_id": 901}}),
        f"/bot{BOT_CREDENTIAL}/sendDocument": reply or json_reply({"ok": True, "result": {"message_id": 902}}),
    }
    with (
        HTTPFixture(routes) as server,
        SpeechAudioHTTPClient(BOT_CREDENTIAL, api_base=server.url(""), allowed_hosts=frozenset({"127.0.0.1"})) as http,
        SpeechModelHTTPClient() as model_http,
        WorkerDatabase(settings) as database,
        WorkerRuntime(settings, clock, namespace, UsageSafetyCaps()) as runtime,
    ):
        engine = WhisperCppEngine(SpeechModelStore(model_dir, model_http), helper_path=helper_path)
        try:
            yield SpeechExecution(
                SpeechJobRunner(
                    runtime.admission,
                    runtime.payloads,
                    runtime.budgets,
                    database.policies,
                    database.ledger,
                    NativeSpeechTransport(NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url("")), http),
                    engine,
                    clock,
                ),
                runtime,
                database,
                server,
            )
        finally:
            engine.close()
