"""Recording synchronous delivery and task drainage helpers for test pipelines."""

import asyncio
from typing import Any

from aiogram import Bot
from aiogram.methods import SendMediaGroup, SendMessage, SendPhoto, SendVideo
from aiogram.types import InputMediaPhoto, InputMediaVideo
from dishka import AsyncContainer

from hovorunv2.application.admission import JobStage
from hovorunv2.application.budgets import RuntimeNamespace
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.feature_jobs import JobOutcome
from hovorunv2.application.media.rendering import build_caption
from hovorunv2.application.worker_io import SyncBrowserPort, SyncHTTPPort, SyncTelegramDeliveryPort
from hovorunv2.bootstrap.worker_runtime import worker_child_context
from hovorunv2.infrastructure.config import Settings
from tests.utils.jobs.envelopes import broker_envelopes
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
        caption = build_caption(payload, platform_name, sender_label)
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
            media = []
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
    with worker_child_context(settings, namespace=namespace, delivery=deliv, http=http, browser=browser) as runner:
        for msg in messages:
            outcome_val = await asyncio.to_thread(runner.run, msg, JobStage.MEDIA)
            browser_msgs = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "browser")
            for b_msg in browser_msgs:
                outcome_val = await asyncio.to_thread(runner.run, b_msg, JobStage.BROWSER)
            outcomes.append(JobOutcome(outcome_val))

    final_outcome = outcomes[0] if len(outcomes) == 1 else JobOutcome.COMPLETE
    calls = deliv.calls if isinstance(deliv, RecordingTelegramDelivery) else []
    return final_outcome, calls
