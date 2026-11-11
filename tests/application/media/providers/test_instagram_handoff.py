"""Verify real Instagram handoff, selection, encrypted retention and settlement."""

import json
import time
from http import HTTPStatus

import pytest
from aiogram import Bot
from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.jobs.admission import JobStage
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, JobOutcome
from hovorunv2.application.media.deadlines import EXTRACTION_CLEANUP_SECONDS, HANDOFF_RESERVE_SECONDS
from hovorunv2.application.media.instagram.embed import build_status_url
from hovorunv2.application.media.instagram.results import INCOMPLETE_MEDIA_TEXT, InstagramPost
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.application.usage.policies import MEDIA_BUCKET, UsageSafetyCaps, UsageUnit
from hovorunv2.domain.enums import CommandName, TargetKind
from hovorunv2.domain.usage_accounting import UsageAccountingEventDB, UsageAggregateDB
from hovorunv2.infrastructure.config import Settings
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.instagram_browser import RoutedInstagramBrowser, route_media_downloads
from tests.utils.instagram_worker import instagram_worker, route_instagram_backend
from tests.utils.jobs import broker_envelopes
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply
from tests.utils.telegram.recording_delivery import RecordingTelegramDelivery
from tests.utils.telegram.session import RecordingBot

pytestmark = [pytest.mark.browser, pytest.mark.slow]
POST = InstagramPost("Synthetic_1", "p")
CHAT_ID = 791
CUTOFF_TOLERANCE_SECONDS = 0.05


def _browser_page(count: int, *, complete: bool) -> str:
    media = [
        {
            "node": {
                "id": f"browser-{index}",
                "__typename": "GraphImage",
                "display_url": f"https://media.example.invalid/browser/{index}.jpg",
            }
        }
        for index in range(count)
    ]
    node = {
        "shortcode": POST.shortcode,
        "edge_sidecar_to_children": {"count": count if complete else count + 1, "edges": media},
        "edge_media_to_caption": {"edges": [{"node": {"text": "Synthetic browser caption"}}]},
    }
    # No custom window globals or carousel attributes: exercise ordinary JSON script discovery.
    return f'<html><body><main></main><script type="application/json">{json.dumps(node)}</script></body></html>'


def _routes(browser_reply: HTTPReply) -> dict[str, HTTPReply]:
    embed = {
        "url": POST.canonical_url,
        "content": "<p>Synthetic embed caption</p>",
        "media_attachments": [
            {"type": "image", "url": f"https://media.example.invalid/embed/{index}.jpg"} for index in range(4)
        ],
    }
    return {
        "/unavailable": HTTPReply(status=HTTPStatus.NOT_FOUND),
        build_status_url(POST, base_url=""): json_reply(embed),
        "/browser": browser_reply,
        **{f"/browser/{index}.jpg": HTTPReply(f"image-{index}".encode()) for index in range(5)},
    }


async def _enqueue(container: AsyncContainer, bot: Bot, chat_id: int) -> None:
    statuses = await container.get(ChatStatusService)
    await statuses.approve(chat_id)
    await authorize_private_media(container, chat_id)
    command = await get_media_command(container, CommandName.INSTAGRAM)
    await command.handle(create_private_media_message(POST.canonical_url, chat_id=chat_id).as_(bot), bot)


async def _assert_single_charge(engine: AsyncEngine, chat_id: int, job_id: str) -> None:
    """Verify one persisted charge and one receipt independently of accounting window rollover."""
    async with engine.connect() as connection:
        total = await connection.scalar(
            select(func.sum(UsageAggregateDB.amount)).where(
                UsageAggregateDB.subject_kind == TargetKind.USER.value,
                UsageAggregateDB.telegram_id == chat_id,
                UsageAggregateDB.bucket == MEDIA_BUCKET,
                UsageAggregateDB.unit == UsageUnit.REQUESTS.value,
            )
        )
        assert total == 1
        receipts = await connection.scalar(
            select(func.count()).select_from(UsageAccountingEventDB).where(UsageAccountingEventDB.event_id == job_id)
        )
        assert receipts == 1


@pytest.mark.parametrize(
    ("browser_count", "complete", "expected_source", "expected_count"),
    [
        (0, False, "embed", 4),
        (1, False, "embed", 4),
        (4, False, "embed", 4),
        (5, False, "browser", 5),
        (1, True, "browser", 1),
    ],
)
async def test_retained_candidate_selection_and_cleanup_across_real_worker_stages(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    browser_count: int,
    test_engine: AsyncEngine,
    *,
    complete: bool,
    expected_source: str,
    expected_count: int,
) -> None:
    """Prefer completeness, then slide count and earlier ties, with one delivery and one charge."""
    chat_id = CHAT_ID + browser_count + (10 if complete else 0)
    await _enqueue(test_container, recording_bot, chat_id)
    settings = await test_container.get(Settings)
    settings = settings.model_copy(
        update={"database_url": postgres_database_url, "browser_enabled": True, "instagram_sessionid": None}
    )
    namespace = await test_container.get(RuntimeNamespace)
    page = (
        _browser_page(browser_count, complete=complete)
        if browser_count
        else "<html><main>Sign in to continue</main></html>"
    )
    delivery = RecordingTelegramDelivery(recording_bot)
    with HTTPFixture(_routes(HTTPReply(page.encode(), headers={"Content-Type": "text/html"}))) as server:
        route_instagram_backend(monkeypatch, server)
        route_media_downloads(monkeypatch, server)
        browser = RoutedInstagramBrowser(server.url("/browser"))
        try:
            with instagram_worker(settings, namespace, delivery, server, browser) as (runner, runtime, _database):
                messages = broker_envelopes(settings, namespace.value)
                assert len(messages) == 1
                envelope = JobEnvelope.model_validate(messages[0])
                assert runner.run(messages[0]) == JobOutcome.COMPLETE.value
                assert runtime.payloads.load_candidate(envelope.job_id) is not None
                stored = runtime.client.get(namespace.value + "payload:candidate:" + envelope.job_id)
                assert isinstance(stored, (str, bytes))
                stored_text = stored.decode("utf-8") if isinstance(stored, bytes) else str(stored)
                assert "Synthetic embed caption" not in stored_text
                assert "media.example.invalid" not in stored_text
                queued = broker_envelopes(settings, namespace.value, "browser")
                assert len(queued) == 1
                assert JobEnvelope.model_validate(queued[0]).deadline == envelope.deadline
                assert "Synthetic embed caption" not in json.dumps(queued[0])
                started = time.monotonic()
                assert runner.run(queued[0], JobStage.BROWSER) == JobOutcome.COMPLETE.value
                assert runtime.payloads.load_candidate(envelope.job_id) is None
                assert runner.run(queued[0], JobStage.BROWSER) == JobOutcome.COMPLETE.value
                assert len(delivery.calls) == 1
                assert len(browser.deadlines) == 1
                deadline = browser.deadlines[0]
                assert deadline is not None
                assert (
                    deadline.expires_at
                    <= started
                    + UsageSafetyCaps().execution_seconds
                    - EXTRACTION_CLEANUP_SECONDS
                    - HANDOFF_RESERVE_SECONDS
                    + CUTOFF_TOLERANCE_SECONDS
                )
        finally:
            browser.close()
        assert not browser.is_running
    await _assert_single_charge(test_engine, chat_id, envelope.job_id)
    payload = delivery.calls[0]["payload"]
    assert isinstance(payload, RichMediaPayload)
    assert [item.url for item in payload.media_items] == [
        f"https://media.example.invalid/{expected_source}/{index}.jpg" for index in range(expected_count)
    ]
    assert payload.footer_text == ("" if complete else INCOMPLETE_MEDIA_TEXT)
    if expected_source == "browser":
        assert [item.data for item in payload.media_items] == [
            f"image-{index}".encode() for index in range(expected_count)
        ]
    assert len(recording_bot.session.calls) == 1


async def test_worker_browser_deadline_aborts_navigation_and_preserves_retained_candidate(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Activate the real worker's browser timeout and reap Chromium before retained delivery."""
    await _enqueue(test_container, recording_bot, CHAT_ID + 20)
    settings = await test_container.get(Settings)
    settings = settings.model_copy(
        update={"database_url": postgres_database_url, "browser_enabled": True, "instagram_sessionid": None}
    )
    namespace = await test_container.get(RuntimeNamespace)
    safety = UsageSafetyCaps(execution_seconds=21)
    routes = _routes(HTTPReply(b"<html>late</html>", delay_seconds=9))
    delivery = RecordingTelegramDelivery(recording_bot)
    with HTTPFixture(routes) as server:
        route_instagram_backend(monkeypatch, server)
        browser = RoutedInstagramBrowser(server.url("/browser"))
        try:
            with instagram_worker(settings, namespace, delivery, server, browser, safety=safety) as (
                runner,
                runtime,
                _database,
            ):
                message = broker_envelopes(settings, namespace.value)[0]
                assert runner.run(message) == JobOutcome.COMPLETE.value
                queued = broker_envelopes(settings, namespace.value, "browser")[0]
                started = time.monotonic()
                assert runner.run(queued, JobStage.BROWSER) == JobOutcome.COMPLETE.value
                assert time.monotonic() - started < 8
                assert not browser.is_running
                assert runtime.payloads.load_candidate(str(message["job_id"])) is None
        finally:
            browser.close()
    assert len(delivery.calls) == 1
    payload = delivery.calls[0]["payload"]
    assert isinstance(payload, RichMediaPayload)
    assert len(payload.media_items) == 4
