"""End-to-end behavioral tests for B05 source-bound Translate and Continue translation buttons."""

import asyncio
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from urllib.parse import parse_qs

import redis
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User
from dishka import AsyncContainer

from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import JobEnvelope, JobStage
from hovorunv2.application.jobs.feature_job_runner import FeatureJobRunner
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.instagram.embed import DisabledEmbedAdapter
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.translation.hybrid_engine import HybridTranslationEngine
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.translation.ports import TranslationEnginePort, TranslationError
from hovorunv2.application.translation.snapshots import (
    SNAPSHOT_TTL_SECONDS,
    TranslationSourceSnapshot,
)
from hovorunv2.application.usage.budgets import RuntimeNamespace, WallClock
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import WorkerQueuePublisher, WorkerRuntime
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, CommandName
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.infrastructure.runtime.feedback_gate import ValkeyFeedbackGate
from hovorunv2.infrastructure.runtime.translation_snapshots import (
    ValkeyTranslationSnapshotStore,
    WorkerTranslationSnapshotClient,
)
from hovorunv2.infrastructure.translation.google_engine import GoogleTranslationEngine
from hovorunv2.infrastructure.translation.language_detector import Py3LangIdDetector
from hovorunv2.interface.telegram.handlers.translate_callbacks import TranslateCallbackHandler
from hovorunv2.interface.telegram.i18n import FluentTranslationCatalog
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI
from tests.conftest import provision_role
from tests.utils.jobs.envelopes import broker_envelopes
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.telegram.recording_delivery import RecordingTelegramDelivery
from tests.utils.telegram.session import RecordingBot


class _NeverCalledLocalEngine(TranslationEnginePort):
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        msg = f"Unexpected local translation for {source_lang}->{target_lang}: {len(text)}"
        raise TranslationError(msg)


def _google_html(translated: str) -> HTTPReply:
    body = f'<html><body><div class="result-container">{translated}</div></body></html>'.encode()
    return HTTPReply(body=body, status=HTTPStatus.OK, headers={"Content-Type": "text/html; charset=utf-8"})


def _make_callback_query(
    bot: RecordingBot,
    *,
    query_id: str,
    user_id: int,
    chat_id: int,
    chat_type: str,
    message_id: int,
    data: str,
    thread_id: int | None = None,
) -> CallbackQuery:
    msg = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type, title="Group" if chat_id < 0 else None),
        from_user=User(id=999999, is_bot=True, first_name="Bot"),
        text="Placeholder",
        message_thread_id=thread_id,
    )
    return CallbackQuery(
        id=query_id,
        from_user=User(id=user_id, is_bot=False, first_name="Clicker"),
        chat_instance="ci-1",
        message=msg,
        data=data,
    ).as_(bot)


def _run_explicit_button_job(
    settings: Settings,
    namespace: RuntimeNamespace,
    raw_envelope: dict[str, object],
    translated_text: str,
) -> tuple[str, list[dict[str, str]]]:
    clock = WallClock()
    safety = UsageSafetyCaps()
    routes = {
        "/m": [_google_html(translated_text)] * 4,
        f"/bot{BOT_CREDENTIAL}/sendMessage": [
            json_reply({"ok": True, "result": {"message_id": 701}}),
            json_reply({"ok": True, "result": {"message_id": 702}}),
        ],
    }
    with (
        HTTPFixture(routes) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http_client,
        GoogleTranslationEngine(base_url=server.url("/m")) as google_engine,
        WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
        WorkerRuntime(settings, clock, namespace, safety) as runtime,
        closing(WorkerQueuePublisher(settings.valkey_url, namespace, safety, clock)) as publisher,
    ):
        hybrid = HybridTranslationEngine(google_engine, _NeverCalledLocalEngine(), online_enabled=True)
        translation = NativeTranslationService(Py3LangIdDetector(), hybrid, settings, database.policies)
        snapshots = WorkerTranslationSnapshotClient(runtime.client, settings, clock, namespace)
        api = NativeTelegramAPI(http_client, BOT_CREDENTIAL, api_base=server.url(""))
        delivery = RecordingTelegramDelivery()
        runner = FeatureJobRunner(
            settings=settings,
            admission=runtime.admission,
            payloads=runtime.payloads,
            budgets=runtime.budgets,
            policies=database.policies,
            ledger=database.ledger,
            http=http_client,
            telegram=api,
            delivery=delivery,
            translation=translation,
            extractor=NativeMediaExtractor(),
            feedback=database.policies,
            gate=ValkeyFeedbackGate(runtime.client, namespace),
            embed=DisabledEmbedAdapter(),
            clock=clock,
            safety=safety,
            browser=None,
            publisher=publisher,
            catalog=FluentTranslationCatalog(),
            snapshots=snapshots,
        )
        outcome = runner.run(raw_envelope, JobStage.MEDIA)
        sent_messages = [
            {k: v[0] for k, v in parse_qs(req.body.decode()).items()}
            for req in server.requests
            if req.path.endswith("/sendMessage")
        ]
        return outcome, sent_messages


async def test_auto_overflow_creates_encrypted_snapshot_and_fits_inline_stays_buttonless(
    test_container: AsyncContainer,
) -> None:
    """Verify auto-translation budget check: fitting stays inline, overflow stores encrypted snapshot + button."""
    settings = await test_container.get(Settings)
    namespace = await test_container.get(RuntimeNamespace)
    clock = WallClock()
    safety = UsageSafetyCaps()
    chat_id = 790101
    users = await test_container.get(UserStore)
    await users.ingest_user(chat_id)
    await users.set_trust(chat_id, is_trusted=True, actor=ActorContext.cli())
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(chat_id)
    await chats.update_translation_settings(chat_id, "uk", [], actor=ActorContext.cli())

    def _run_sync() -> tuple[str | None, str | None, bytes | str | None]:
        with (
            HTTPFixture(
                {
                    "/m": [_google_html("Короткий переклад."), _google_html("Довгий переклад " * 90)],
                    f"/bot{BOT_CREDENTIAL}/sendPhoto": json_reply({"ok": True, "result": {"message_id": 501}}),
                }
            ) as server,
            NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http_client,
            GoogleTranslationEngine(base_url=server.url("/m")) as google_engine,
            WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
            closing(WorkerQueuePublisher(settings.valkey_url, namespace, safety, clock)) as publisher,
        ):
            hybrid = HybridTranslationEngine(google_engine, _NeverCalledLocalEngine(), online_enabled=True)
            translation = NativeTranslationService(Py3LangIdDetector(), hybrid, settings, database.policies)
            snapshots = WorkerTranslationSnapshotClient(runtime.client, settings, clock, namespace)
            api = NativeTelegramAPI(http_client, BOT_CREDENTIAL, api_base=server.url(""))
            delivery = RecordingTelegramDelivery()
            runner = FeatureJobRunner(
                settings=settings,
                admission=runtime.admission,
                payloads=runtime.payloads,
                budgets=runtime.budgets,
                policies=database.policies,
                ledger=database.ledger,
                http=http_client,
                telegram=api,
                delivery=delivery,
                translation=translation,
                extractor=NativeMediaExtractor(),
                feedback=database.policies,
                gate=ValkeyFeedbackGate(runtime.client, namespace),
                embed=DisabledEmbedAdapter(),
                clock=clock,
                safety=safety,
                browser=None,
                publisher=publisher,
                catalog=FluentTranslationCatalog(),
                snapshots=snapshots,
            )
            short_payload = RichMediaPayload(
                author_name="Author",
                author_handle="author",
                author_url="https://example.com/a",
                original_url="https://example.com/p/1",
                content="This is a short English sentence for inline translation.",
                media_items=[MediaItem(url="https://example.com/1.jpg", is_video=False)],
            )
            now_ts = clock.now()
            env_short = JobEnvelope.model_validate(
                {
                    "job_id": "jobshort1",
                    "payload_ref": "a" * 32,
                    "feature": CommandName.TWITTER_POST,
                    "target": {"kind": "user", "entity_id": 1, "generation": 1, "telegram_id": chat_id},
                    "actor_id": chat_id,
                    "actor_generation": 1,
                    "trigger": "direct",
                    "charged_kind": "user",
                    "charged_telegram_id": chat_id,
                    "accepted_at": now_ts,
                    "deadline": now_ts + 300,
                    "dedupe_key": "b" * 64,
                    "feature_epoch": 0,
                    "message_id": 11,
                }
            )
            markup_short = runner.prepare_auto_translation_markup(
                short_payload, env_short, "Twitter", "Sender", clock.now(), strip_hashtags=False
            )
            long_text = "This is a long English caption about our engineering systems. " * 15
            long_payload = RichMediaPayload(
                author_name="Author",
                author_handle="author",
                author_url="https://example.com/a",
                original_url="https://example.com/p/2",
                content=long_text,
                media_items=[MediaItem(url="https://example.com/2.jpg", is_video=False)],
            )
            markup_long = runner.prepare_auto_translation_markup(
                long_payload, env_short, "Twitter", "Sender", clock.now(), strip_hashtags=False
            )
            sync_redis = redis.from_url(settings.valkey_url)
            try:
                keys = sync_redis.keys(f"{namespace.value}tr:src:*")
                raw_stored = sync_redis.get(keys[0]) if keys else None
            finally:
                sync_redis.close()
            assert "Короткий переклад." in (short_payload.content or "")
            assert "Довгий переклад" not in (long_payload.content or "")
            return markup_short, markup_long, raw_stored

    markup_short, markup_long, raw_stored = await asyncio.to_thread(_run_sync)
    assert markup_short is None
    assert markup_long is not None
    parsed = json.loads(markup_long)
    assert parsed["inline_keyboard"][0][0]["callback_data"].startswith("tr:s:")
    assert raw_stored is not None
    raw_bytes = raw_stored if isinstance(raw_stored, bytes) else raw_stored.encode("utf-8")
    assert b"engineering systems" not in raw_bytes


async def _seed_group_for_buttons(
    test_container: AsyncContainer, group_id: int, member_one: int, member_two: int, outsider: int
) -> None:
    users = await test_container.get(UserStore)
    for uid in (member_one, member_two, outsider):
        await users.ingest_user(uid)
    await provision_role(test_container, member_one)
    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(group_id)
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await chats.update_translation_settings(group_id, "uk", [], actor=ActorContext.cli())
    await (await test_container.get(GroupGrantStore)).approve(chat.id, ActorContext.cli())
    now = datetime.now(UTC) - timedelta(seconds=1)
    expiry = now + timedelta(minutes=5)
    ev1 = MembershipEvidence(group_id, member_one, MembershipStatus.ADMIN, now, expiry)
    ev2 = MembershipEvidence(group_id, member_two, MembershipStatus.ADMIN, now, expiry)
    await (await test_container.get(MembershipStore)).save_admins(
        chat.id, AdministratorEvidence(group_id, True, (ev1, ev2), now, expiry), ActorContext.cli()
    )


async def _verify_cross_chat_and_expiry(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
    handler: TranslateCallbackHandler,
    settings: Settings,
    namespace: RuntimeNamespace,
    src_token: str,
    cb_data: str,
    member_one: int,
    base_now: float,
) -> None:
    q_cross = _make_callback_query(
        recording_bot,
        query_id="cb-4",
        user_id=member_one,
        chat_id=-100999999,
        chat_type="supergroup",
        message_id=50,
        data=f"tr:s:{src_token}",
    )
    await handler.handle_callback(q_cross, recording_bot)
    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert answers[-1].show_alert is True

    async with runtime_cache(test_container) as (cache, _):
        expired_clock = ManualClock(base_now + SNAPSHOT_TTL_SECONDS + 1)
        expired_store = ValkeyTranslationSnapshotStore(cache, settings, expired_clock, namespace)
        assert await expired_store.get_source(src_token) is None
        chain_token = cb_data.split(":")[2]
        assert await expired_store.get_chain(chain_token) is None


async def test_group_translate_button_and_multipart_continuation_chain(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Exercise Translate button -> multi-part Continue translation chain -> shared member continuation -> expiry."""
    member_one, member_two, outsider = 790201, 790202, 790203
    group_id = -100790204
    await _seed_group_for_buttons(test_container, group_id, member_one, member_two, outsider)

    settings = await test_container.get(Settings)
    namespace = await test_container.get(RuntimeNamespace)
    base_now = WallClock().now()

    def _seed_snapshot() -> str:
        sync_redis = redis.from_url(settings.valkey_url)
        try:
            client = WorkerTranslationSnapshotClient(sync_redis, settings, WallClock(), namespace)
            snap = TranslationSourceSnapshot(
                chat_id=group_id,
                owner_id=None,
                source_message_id=50,
                thread_id=7,
                target_lang="uk",
                ui_locale="uk",
                text=(
                    "Today our engineering team published a detailed report about distributed translation "
                    "pipelines and automated verification across production environments. "
                )
                * 8,
                created_at=base_now,
                expires_at=base_now + SNAPSHOT_TTL_SECONDS,
            )
            return client.save_source(snap)
        finally:
            sync_redis.close()

    src_token = await asyncio.to_thread(_seed_snapshot)
    handler = await test_container.get(TranslateCallbackHandler)

    # Member 1 clicks Translate button
    q1 = _make_callback_query(
        recording_bot,
        query_id="cb-1",
        user_id=member_one,
        chat_id=group_id,
        chat_type="supergroup",
        message_id=50,
        data=f"tr:s:{src_token}",
        thread_id=7,
    )
    await handler.handle_callback(q1, recording_bot)
    envelopes = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "media")
    translate_envelopes = [env for env in envelopes if JobEnvelope.model_validate(env).feature == CommandName.TRANSLATE]
    assert len(translate_envelopes) == 1

    # Run worker producing > 4096 chars -> Part 1 sent with Continue translation button
    long_translation = "Український переклад довгого абзацу тексту. " * 110
    outcome, sent_msgs = await asyncio.to_thread(
        _run_explicit_button_job, settings, namespace, translate_envelopes[0], long_translation
    )
    assert outcome == JobOutcome.COMPLETE
    assert len(sent_msgs) == 1
    assert "reply_markup" in sent_msgs[0]
    markup = json.loads(sent_msgs[0]["reply_markup"])
    cb_data: str = markup["inline_keyboard"][0][0]["callback_data"]
    assert cb_data.startswith("tr:c:")

    # Member 2 clicks Continue translation -> receives Part 2 as reply to Part 1 (701), no button on final part
    q2 = _make_callback_query(
        recording_bot,
        query_id="cb-2",
        user_id=member_two,
        chat_id=group_id,
        chat_type="supergroup",
        message_id=701,
        data=cb_data,
        thread_id=7,
    )
    await handler.handle_callback(q2, recording_bot)
    sent_followups = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert len(sent_followups) == 1
    assert sent_followups[0].reply_to_message_id == 701
    assert sent_followups[0].message_thread_id == 7
    assert isinstance(sent_followups[0].reply_markup, InlineKeyboardMarkup)
    next_cb = sent_followups[0].reply_markup.inline_keyboard[0][0].callback_data
    assert next_cb is not None

    # Stale/duplicate click on Part 1 cb_data does not send another message
    q3 = _make_callback_query(
        recording_bot,
        query_id="cb-3",
        user_id=member_one,
        chat_id=group_id,
        chat_type="supergroup",
        message_id=701,
        data=cb_data,
        thread_id=7,
    )
    await handler.handle_callback(q3, recording_bot)
    assert len([c for c in recording_bot.session.calls if isinstance(c, SendMessage)]) == 1

    # Click Part 2 Continue translation -> receives final Part 3 with no button
    q_final = _make_callback_query(
        recording_bot,
        query_id="cb-3b",
        user_id=member_one,
        chat_id=group_id,
        chat_type="supergroup",
        message_id=1,
        data=next_cb,
        thread_id=7,
    )
    await handler.handle_callback(q_final, recording_bot)
    sent_followups = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert len(sent_followups) == 2
    assert sent_followups[1].reply_to_message_id == 1
    assert sent_followups[1].reply_markup is None

    await _verify_cross_chat_and_expiry(
        test_container,
        recording_bot,
        handler,
        settings,
        namespace,
        src_token,
        cb_data,
        member_one,
        base_now,
    )
