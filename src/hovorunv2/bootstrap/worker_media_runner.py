"""Feature job runner and media extraction composition for native media/browser workers."""

from contextlib import ExitStack

import redis

from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.jobs.feature_job_runner import FeatureJobRunner
from hovorunv2.application.jobs.http_ports import (
    SyncBrowserPort,
    SyncHTTPPort,
    SyncQueuePublisherPort,
    SyncTelegramDeliveryPort,
)
from hovorunv2.application.media.downloader import NativeMediaDownloader
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.instagram.embed import DisabledEmbedAdapter
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.usage.budgets import Clock, RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.media.embed_adapter import VxInstagramEmbedAdapter
from hovorunv2.infrastructure.runtime.admission import WorkerAdmissionClient
from hovorunv2.infrastructure.runtime.budgets import WorkerBudgetClient
from hovorunv2.infrastructure.runtime.feature_payloads import WorkerPayloadClient
from hovorunv2.infrastructure.runtime.feedback_gate import ValkeyFeedbackGate
from hovorunv2.infrastructure.translation.ctranslate2_engine import CTranslate2Engine
from hovorunv2.infrastructure.translation.language_detector import Py3LangIdDetector
from hovorunv2.infrastructure.translation.model_store import MODEL_DOWNLOAD_SECONDS, ModelHTTPClient, ModelStore
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI
from hovorunv2.interface.telegram.transport.sync_delivery import NativeTelegramDelivery


def create_feature_runner(
    settings: Settings,
    resources: ExitStack,
    admission: WorkerAdmissionClient,
    payloads: WorkerPayloadClient,
    budgets: WorkerBudgetClient,
    client: redis.Redis,
    db: WorkerDatabase,
    http: SyncHTTPPort,
    api: NativeTelegramAPI,
    clock: Clock,
    safety: UsageSafetyCaps,
    namespace: RuntimeNamespace,
    browser: SyncBrowserPort | None,
    publisher: SyncQueuePublisherPort,
    custom_delivery: SyncTelegramDeliveryPort | None = None,
) -> FeatureJobRunner:
    """Compose the FeatureJobRunner with its synchronous media extraction and delivery adapters.

    Args:
        settings: Application runtime settings.
        resources: Child-owned resource stack that closes the translation engine and transport.
        admission: Child-owned admission client.
        payloads: Child-owned encrypted payload client.
        budgets: Child-owned budget reservation client.
        client: Child-owned raw Valkey client.
        db: Child-owned worker database adapters.
        http: Child-owned synchronous HTTP transport.
        api: Native Telegram Bot API adapter.
        clock: Time provider.
        safety: Usage safety limits.
        namespace: Runtime namespace.
        browser: Optional synchronous browser port.
        publisher: Synchronous task queue publisher.
        custom_delivery: Optional custom delivery port for testing.

    Returns:
        Configured FeatureJobRunner ready to orchestrate stage execution.
    """
    downloader = NativeMediaDownloader(http, destination_policy=http)
    delivery = custom_delivery or NativeTelegramDelivery(api, downloader)
    model_http = resources.enter_context(ModelHTTPClient(total_timeout=MODEL_DOWNLOAD_SECONDS))
    engine = CTranslate2Engine(ModelStore(settings.translation_model_dir, model_http))
    resources.callback(engine.close)
    translation = NativeTranslationService(Py3LangIdDetector(), engine, settings, db.policies)
    extractor = NativeMediaExtractor()
    embed = VxInstagramEmbedAdapter() if settings.vxinstagram_enabled else DisabledEmbedAdapter()
    return FeatureJobRunner(
        admission=admission,
        payloads=payloads,
        budgets=budgets,
        policies=db.policies,
        ledger=db.ledger,
        http=http,
        delivery=delivery,
        translation=translation,
        extractor=extractor,
        clock=clock,
        settings=settings,
        registry=FEATURE_REGISTRY,
        browser=browser,
        publisher=publisher,
        telegram=api,
        feedback=db.policies,
        gate=ValkeyFeedbackGate(client, namespace),
        embed=embed,
        safety=safety,
    )
