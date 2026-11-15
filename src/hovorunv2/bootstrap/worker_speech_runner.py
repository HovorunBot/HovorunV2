"""Speech job runner composition for native speech worker child processes."""

from collections.abc import Callable
from contextlib import ExitStack

import redis

from hovorunv2.application.speech.runner import SpeechJobRunner
from hovorunv2.application.usage.budgets import Clock, RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.admission import WorkerAdmissionClient
from hovorunv2.infrastructure.runtime.budgets import WorkerBudgetClient
from hovorunv2.infrastructure.runtime.feature_payloads import WorkerPayloadClient
from hovorunv2.infrastructure.speech.audio_http import SpeechAudioHTTPClient
from hovorunv2.infrastructure.speech.model_store import MODEL_DOWNLOAD_SECONDS, SpeechModelHTTPClient, SpeechModelStore
from hovorunv2.infrastructure.speech.whisper_engine import CTranslate2WhisperEngine
from hovorunv2.interface.telegram.transport.speech import NativeSpeechTransport
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI


def create_speech_runner(
    settings: Settings,
    resources: ExitStack,
    admission: WorkerAdmissionClient,
    payloads: WorkerPayloadClient,
    budgets: WorkerBudgetClient,
    client: redis.Redis,
    db: WorkerDatabase,
    api: NativeTelegramAPI,
    clock: Clock,
    safety: UsageSafetyCaps,
    namespace: RuntimeNamespace,
    *,
    idle_hooks: list[Callable[[], bool]] | None = None,
) -> SpeechJobRunner:
    """Compose the SpeechJobRunner with its child-owned engine and dependencies.

    Args:
        settings: Application runtime settings.
        resources: Child-owned resource stack that cleans up the engine.
        admission: Child-owned admission client.
        payloads: Child-owned encrypted payload client.
        budgets: Child-owned budget reservation client.
        client: Child-owned raw Valkey client.
        db: Child-owned worker database adapters.
        api: Native Telegram Bot API adapter.
        clock: Time provider.
        safety: Usage safety limits.
        namespace: Runtime namespace.
        idle_hooks: Optional list receiving the engine's idle-unload callback.

    Returns:
        Configured SpeechJobRunner ready to orchestrate speech recognition.
    """
    model_http = resources.enter_context(SpeechModelHTTPClient(total_timeout=MODEL_DOWNLOAD_SECONDS))
    model_store = SpeechModelStore(settings.speech_model_dir, model_http)
    model_store.require_ready()
    engine = CTranslate2WhisperEngine(
        model_store,
        idle_timeout_seconds=settings.speech_model_idle_timeout,
        clock=clock,
    )
    if idle_hooks is not None:
        idle_hooks.append(engine.unload_idle)
    audio_http = resources.enter_context(SpeechAudioHTTPClient(settings.bot_token, clock=clock))
    speech_api = NativeTelegramAPI(audio_http, settings.bot_token)
    resources.callback(engine.close)
    return SpeechJobRunner(
        admission=admission,
        payloads=payloads,
        budgets=budgets,
        policies=db.policies,
        ledger=db.ledger,
        telegram=NativeSpeechTransport(speech_api, audio_http, clock=clock),
        engine=engine,
        clock=clock,
    )
