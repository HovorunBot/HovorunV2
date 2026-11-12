"""Speech job runner composition for native speech worker child processes."""

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
from hovorunv2.infrastructure.speech.whisper_engine import WhisperCppEngine
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
) -> SpeechJobRunner:
    """Compose the SpeechJobRunner with its child-owned engine and dependencies.

    Args:
        settings: Application runtime settings.
        resources: Child-owned resource stack that cleans up the engine helper.
        admission: Child-owned admission client.
        payloads: Child-owned encrypted payload client.
        budgets: Child-owned budget reservation client.
        client: Child-owned raw Valkey client.
        db: Child-owned worker database adapters.
        api: Native Telegram Bot API adapter.
        clock: Time provider.
        safety: Usage safety limits.
        namespace: Runtime namespace.

    Returns:
        Configured SpeechJobRunner ready to orchestrate speech recognition.

    Raises:
        FileNotFoundError: If the native helper has not been installed.
    """
    model_http = resources.enter_context(SpeechModelHTTPClient(total_timeout=MODEL_DOWNLOAD_SECONDS))
    model_store = SpeechModelStore(settings.speech_model_dir, model_http)
    model_store.require_ready()
    if not settings.speech_helper_path.is_file():
        msg = "The speech helper must be installed before worker startup."
        raise FileNotFoundError(msg)
    engine = WhisperCppEngine(
        model_store, helper_path=settings.speech_helper_path, idle_timeout_seconds=settings.speech_model_idle_timeout
    )
    audio_http = resources.enter_context(SpeechAudioHTTPClient(settings.bot_token))
    speech_api = NativeTelegramAPI(audio_http, settings.bot_token)
    resources.callback(engine.close)
    return SpeechJobRunner(
        admission=admission,
        payloads=payloads,
        budgets=budgets,
        policies=db.policies,
        ledger=db.ledger,
        telegram=NativeSpeechTransport(speech_api, audio_http),
        engine=engine,
        clock=clock,
    )
