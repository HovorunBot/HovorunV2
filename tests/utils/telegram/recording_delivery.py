"""Recording synchronous delivery and task drainage helpers for test pipelines."""

import asyncio
from collections.abc import Generator
from contextlib import ExitStack, closing, contextmanager
from http import HTTPStatus
from typing import Any

from aiogram import Bot
from aiogram.methods import SendMediaGroup, SendMessage, SendPhoto, SendVideo
from aiogram.methods.send_media_group import MediaUnion
from aiogram.types import InputMediaPhoto, InputMediaVideo
from dishka import AsyncContainer

from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.jobs.admission import JobStage
from hovorunv2.application.jobs.feature_job_runner import FeatureJobRunner
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.application.jobs.http_ports import SyncBrowserPort, SyncHTTPPort, SyncTelegramDeliveryPort
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.instagram.embed import DisabledEmbedAdapter
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.rendering import build_caption
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.usage.budgets import RuntimeNamespace, WallClock
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import LazyBrowserService, WorkerQueuePublisher, WorkerRuntime
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.infrastructure.media.embed_adapter import VxInstagramEmbedAdapter
from hovorunv2.infrastructure.runtime.feedback_gate import ValkeyFeedbackGate
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI
from tests.utils.jobs.envelopes import broker_envelopes
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.telegram.session import RecordingSession


class RecordingTelegramDelivery(SyncTelegramDeliveryPort):
    """Synchronous test delivery recording real aiogram method calls."""

    def __init__(self, bot: Bot | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._bot = bot

    def deliver(
        self,
        chat_id: int,
        message_id: int,
        payload: RichMediaPayload,
        platform_name: str,
        sender_label: str | None,
    ) -> tuple[int, ...]:
        """Convert payload to real aiogram method calls and record on recording_bot."""
        caption = build_caption(payload, sender_label, platform_name)
        method: Any
        if not payload.media_items:
            method = SendMessage(
                chat_id=chat_id,
                reply_to_message_id=message_id,
                text=caption,
            )
        elif len(payload.media_items) == 1:
            item = payload.media_items[0]
            if item.is_video:
                method = SendVideo(
                    chat_id=chat_id,
                    reply_to_message_id=message_id,
                    video=item.url,
                    caption=caption,
                )
            else:
                method = SendPhoto(
                    chat_id=chat_id,
                    reply_to_message_id=message_id,
                    photo=item.url,
                    caption=caption,
                )
        else:
            media: list[MediaUnion] = []
            for i, item in enumerate(payload.media_items):
                item_caption = caption if i == 0 else None
                if item.is_video:
                    media.append(InputMediaVideo(media=item.url, caption=item_caption))
                else:
                    media.append(InputMediaPhoto(media=item.url, caption=item_caption))
            method = SendMediaGroup(
                chat_id=chat_id,
                reply_to_message_id=message_id,
                media=media,
            )

        self.calls.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "payload": payload,
                "platform_name": platform_name,
                "sender_label": sender_label,
                "method": method,
            }
        )
        if self._bot is not None and isinstance(self._bot.session, RecordingSession):
            self._bot.session.calls.append(method)
        return (1000 + len(self.calls),)


@contextmanager
def _recording_worker(
    settings: Settings,
    namespace: RuntimeNamespace,
    delivery: SyncTelegramDeliveryPort,
    http: SyncHTTPPort | None,
    browser: SyncBrowserPort | None,
) -> Generator[FeatureJobRunner]:
    """Compose real worker services with synthetic local Telegram progress responses."""
    clock = WallClock()
    safety = UsageSafetyCaps()
    routes = {
        f"/bot{BOT_CREDENTIAL}/sendMessage": json_reply({"ok": True, "result": {"message_id": 100}}),
        "/translate": HTTPReply(status=HTTPStatus.SERVICE_UNAVAILABLE),
    }
    with (
        ExitStack() as resources,
        HTTPFixture(routes) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as telegram_http,
        WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
        WorkerRuntime(settings, clock, namespace, safety) as runtime,
        closing(WorkerQueuePublisher(settings.valkey_url, namespace, safety, clock)) as publisher,
    ):
        provider_http = http if http is not None else resources.enter_context(NativeHTTPClient())
        worker_browser = browser
        if worker_browser is None and settings.browser_enabled:
            worker_browser = LazyBrowserService()
            resources.callback(worker_browser.close)
        yield FeatureJobRunner(
            settings=settings,
            admission=runtime.admission,
            payloads=runtime.payloads,
            budgets=runtime.budgets,
            policies=database.policies,
            ledger=database.ledger,
            http=provider_http,
            telegram=NativeTelegramAPI(telegram_http, BOT_CREDENTIAL, api_base=server.url("")),
            translation=NativeTranslationService(
                telegram_http, settings, database.policies, api_url=server.url("/translate")
            ),
            extractor=NativeMediaExtractor(),
            feedback=database.policies,
            gate=ValkeyFeedbackGate(runtime.client, namespace),
            embed=VxInstagramEmbedAdapter() if settings.vxinstagram_enabled else DisabledEmbedAdapter(),
            clock=clock,
            safety=safety,
            browser=worker_browser,
            publisher=publisher,
            delivery=delivery,
        )


async def drain_and_run_media(
    test_container: AsyncContainer,
    *,
    recording_bot: Bot | None = None,
    http: SyncHTTPPort | None = None,
    delivery: SyncTelegramDeliveryPort | None = None,
    browser: SyncBrowserPort | None = None,
) -> tuple[JobOutcome, list[dict[str, object]]]:
    """Drain enqueued media jobs and execute through child-local worker context."""
    settings = await test_container.get(Settings)
    namespace = await test_container.get(RuntimeNamespace)
    messages = await asyncio.to_thread(broker_envelopes, settings, namespace.value)
    if not messages:
        return JobOutcome.COMPLETE, []

    deliv = delivery or RecordingTelegramDelivery(recording_bot)
    outcomes: list[JobOutcome] = []
    with _recording_worker(settings, namespace, deliv, http, browser) as runner:
        for msg in messages:
            outcome_val = runner.run(msg, JobStage.MEDIA)
            browser_msgs = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "browser")
            for b_msg in browser_msgs:
                outcome_val = runner.run(b_msg, JobStage.BROWSER)
            compression_msgs = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "compression")
            for compression_msg in compression_msgs:
                outcome_val = runner.run(compression_msg, JobStage.COMPRESSION)
            outcomes.append(JobOutcome(outcome_val))

    final_outcome = outcomes[0] if len(outcomes) == 1 else JobOutcome.COMPLETE
    calls = deliv.calls if isinstance(deliv, RecordingTelegramDelivery) else []
    return final_outcome, calls
