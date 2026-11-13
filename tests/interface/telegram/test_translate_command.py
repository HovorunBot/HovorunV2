"""End-to-end behavioral tests for reply-based /translate [target] requests."""

import asyncio
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from urllib.parse import parse_qs

import pytest
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, TextQuote, User
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
from hovorunv2.application.media.rendering import extract_rendered_post_content
from hovorunv2.application.translation.hybrid_engine import HybridTranslationEngine
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.translation.ports import (
    CancellationToken,
    TranslationBudget,
    TranslationEnginePort,
    TranslationError,
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
from hovorunv2.infrastructure.translation.google_engine import GoogleTranslationEngine
from hovorunv2.infrastructure.translation.language_detector import Py3LangIdDetector
from hovorunv2.interface.telegram.handlers.translate import TranslateCommand
from hovorunv2.interface.telegram.i18n import AudienceI18nManager, FluentTranslationCatalog
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI
from tests.conftest import provision_role
from tests.utils.jobs.envelopes import broker_envelopes
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.telegram.recording_delivery import RecordingTelegramDelivery
from tests.utils.telegram.session import RecordingBot


class _NeverCalledLocalEngine(TranslationEnginePort):
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
        msg = f"Unexpected local translation for {source_lang}->{target_lang}: {len(text)}"
        raise TranslationError(msg)


def _google_html(translated: str) -> HTTPReply:
    body = f'<html><body><div class="result-container">{translated}</div></body></html>'.encode()
    return HTTPReply(body=body, status=HTTPStatus.OK, headers={"Content-Type": "text/html; charset=utf-8"})


def _make_reply_source(
    bot: RecordingBot,
    *,
    chat_id: int,
    chat_type: str,
    user_id: int,
    message_id: int,
    text: str | None = None,
    caption: str | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type, title="Group" if chat_id < 0 else None),
        from_user=User(id=user_id, is_bot=False, first_name="Author"),
        text=text,
        caption=caption,
    ).as_(bot)


def _make_command_message(
    bot: RecordingBot,
    *,
    chat_id: int,
    chat_type: str,
    user_id: int,
    message_id: int,
    text: str,
    reply_to_message: Message | None = None,
    quote_text: str | None = None,
) -> Message:
    full_source = (reply_to_message.text or reply_to_message.caption or "") if reply_to_message is not None else ""
    quote_offset = full_source.find(quote_text) if quote_text is not None else -1
    position = len(full_source[:quote_offset].encode("utf-16-le")) // 2 if quote_offset >= 0 else 0
    quote = TextQuote(text=quote_text, position=position) if quote_text is not None else None
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type, title="Group" if chat_id < 0 else None),
        from_user=User(id=user_id, is_bot=False, first_name="Requester"),
        text=text,
        reply_to_message=reply_to_message,
        quote=quote,
    ).as_(bot)


def _run_translate_envelope(
    settings: Settings, namespace: RuntimeNamespace, raw_envelope: dict[str, object]
) -> tuple[str, list[str], list[str]]:
    clock = WallClock()
    safety = UsageSafetyCaps()
    routes = {
        "/m": [_google_html("Hallo, hier ist ein Foto unserer Reise nach Frankreich.")],
        f"/bot{BOT_CREDENTIAL}/sendMessage": json_reply({"ok": True, "result": {"message_id": 100}}),
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
        runner = FeatureJobRunner(
            settings=settings,
            admission=runtime.admission,
            payloads=runtime.payloads,
            budgets=runtime.budgets,
            policies=database.policies,
            ledger=database.ledger,
            http=http_client,
            telegram=NativeTelegramAPI(http_client, BOT_CREDENTIAL, api_base=server.url("")),
            translation=translation,
            extractor=NativeMediaExtractor(),
            feedback=database.policies,
            gate=ValkeyFeedbackGate(runtime.client, namespace),
            embed=DisabledEmbedAdapter(),
            clock=clock,
            safety=safety,
            browser=None,
            publisher=publisher,
            delivery=RecordingTelegramDelivery(),
            catalog=FluentTranslationCatalog(),
        )
        outcome = runner.run(raw_envelope, JobStage.MEDIA)
        google_paths = [req.path for req in server.requests if req.path.startswith("/m")]
        sent_texts = [
            parse_qs(req.body.decode())["text"][0] for req in server.requests if req.path.endswith("/sendMessage")
        ]
        return outcome, google_paths, sent_texts


async def test_translate_command_validates_reply_quote_and_target_precedence(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Validate quote vs full source, mismatched quotes, invalid targets, and independent UI/content locales."""
    user_id = 780101
    users = await test_container.get(UserStore)
    await users.ingest_user(user_id)
    await users.set_trust(user_id, is_trusted=True, actor=ActorContext.cli())
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(user_id)
    await chats.update_translation_settings(user_id, "uk", ["fr"], actor=ActorContext.cli())
    i18n = await test_container.get(AudienceI18nManager)
    await i18n.set_user_locale(user_id, "en")

    cmd = await test_container.get(TranslateCommand)
    no_reply = _make_command_message(
        recording_bot, chat_id=user_id, chat_type="private", user_id=user_id, message_id=10, text="/translate"
    )
    await cmd.handle(no_reply, recording_bot)
    sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert "Reply to a message" in sent[-1].text

    source = _make_reply_source(
        recording_bot,
        chat_id=user_id,
        chat_type="private",
        user_id=user_id,
        message_id=11,
        text="English intro. Bonjour, voici une photographie de notre voyage en France.",
    )
    bad_target = _make_command_message(
        recording_bot,
        chat_id=user_id,
        chat_type="private",
        user_id=user_id,
        message_id=12,
        text="/translate <bad>",
        reply_to_message=source,
    )
    await cmd.handle(bad_target, recording_bot)
    sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert "&lt;bad&gt;" in sent[-1].text

    mismatched_quote = _make_command_message(
        recording_bot,
        chat_id=user_id,
        chat_type="private",
        user_id=user_id,
        message_id=13,
        text="/translate",
        reply_to_message=source,
        quote_text="Unrelated quote not in source",
    )
    await cmd.handle(mismatched_quote, recording_bot)
    sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert "No translatable text" in sent[-1].text
    assert "English intro" not in sent[-1].text

    quote_cmd = _make_command_message(
        recording_bot,
        chat_id=user_id,
        chat_type="private",
        user_id=user_id,
        message_id=14,
        text="/translate de",
        reply_to_message=source,
        quote_text="Bonjour, voici une photographie de notre voyage en France.",
    )
    await cmd.handle(quote_cmd, recording_bot)

    settings = await test_container.get(Settings)
    namespace = await test_container.get(RuntimeNamespace)
    envelopes = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "media")
    translate_envelopes = [env for env in envelopes if JobEnvelope.model_validate(env).feature == CommandName.TRANSLATE]
    assert len(translate_envelopes) == 1
    outcome, google_paths, sent_texts = await asyncio.to_thread(
        _run_translate_envelope, settings, namespace, translate_envelopes[0]
    )
    assert outcome == JobOutcome.COMPLETE
    assert len(google_paths) == 1
    assert "English+intro" not in google_paths[0]
    assert any("Hallo, hier ist ein Foto unserer Reise nach Frankreich." in t for t in sent_texts)

    persisted_chat = await chats.get_chat(user_id)
    assert persisted_chat is not None
    assert persisted_chat.target_lang == "uk"
    assert persisted_chat.ignored_langs == ["fr"]


async def test_translate_command_deduplicates_concurrent_group_members_and_enforces_access(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Share group work across authorized members for identical source/scope/target and deny unauthorized users."""
    member_one, member_two, outsider = 780201, 780202, 780203
    group_id = -100780204
    users = await test_container.get(UserStore)
    for uid in (member_one, member_two, outsider):
        await users.ingest_user(uid)
    await provision_role(test_container, member_one)

    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(group_id)
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await (await test_container.get(GroupGrantStore)).approve(chat.id, ActorContext.cli())
    now = datetime.now(UTC) - timedelta(seconds=1)
    expiry = now + timedelta(minutes=5)
    ev1 = MembershipEvidence(group_id, member_one, MembershipStatus.ADMIN, now, expiry)
    ev2 = MembershipEvidence(group_id, member_two, MembershipStatus.ADMIN, now, expiry)
    await (await test_container.get(MembershipStore)).save_admins(
        chat.id, AdministratorEvidence(group_id, True, (ev1, ev2), now, expiry), ActorContext.cli()
    )

    cmd = await test_container.get(TranslateCommand)
    source = _make_reply_source(
        recording_bot,
        chat_id=group_id,
        chat_type="supergroup",
        user_id=member_one,
        message_id=50,
        caption="This is an English caption on a group media post.",
    )
    req1 = _make_command_message(
        recording_bot,
        chat_id=group_id,
        chat_type="supergroup",
        user_id=member_one,
        message_id=51,
        text="/translate",
        reply_to_message=source,
    )
    req2 = _make_command_message(
        recording_bot,
        chat_id=group_id,
        chat_type="supergroup",
        user_id=member_two,
        message_id=52,
        text="/translate",
        reply_to_message=source,
    )
    unapproved_group_id = -100780299
    await chats.get_or_create_chat(unapproved_group_id)
    unapproved_source = _make_reply_source(
        recording_bot,
        chat_id=unapproved_group_id,
        chat_type="supergroup",
        user_id=outsider,
        message_id=60,
        caption="Unauthorized group caption.",
    )
    req_outsider = _make_command_message(
        recording_bot,
        chat_id=unapproved_group_id,
        chat_type="supergroup",
        user_id=outsider,
        message_id=61,
        text="/translate",
        reply_to_message=unapproved_source,
    )
    await cmd.handle(req1, recording_bot)
    await cmd.handle(req2, recording_bot)
    await cmd.handle(req_outsider, recording_bot)

    group_sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == group_id]
    assert len(group_sent) == 1
    assert "Перекладаю..." in group_sent[0].text
    denied_sent = [
        c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == unapproved_group_id
    ]
    assert len(denied_sent) == 1
    assert "немає доступу" in denied_sent[0].text
    settings = await test_container.get(Settings)
    namespace = await test_container.get(RuntimeNamespace)
    envelopes = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "media")
    translate_envelopes = [env for env in envelopes if JobEnvelope.model_validate(env).feature == CommandName.TRANSLATE]
    assert len(translate_envelopes) == 1


@pytest.mark.parametrize(("position", "expected_jobs"), [(1, 0), (3, 1)])
async def test_translate_validates_quote_utf16_position(
    test_container: AsyncContainer, recording_bot: RecordingBot, position: int, expected_jobs: int
) -> None:
    """Admit only selected text that matches its Telegram UTF-16 offset, including astral characters."""
    user_id = 780401
    await (await test_container.get(ChatStore)).get_or_create_chat(user_id)
    users = await test_container.get(UserStore)
    await users.ingest_user(user_id)
    await users.set_trust(user_id, is_trusted=True, actor=ActorContext.cli())
    source = _make_reply_source(
        recording_bot,
        chat_id=user_id,
        chat_type="private",
        user_id=user_id,
        message_id=41,
        text="😀 Bonjour tout le monde.",
    )
    command = _make_command_message(
        recording_bot,
        chat_id=user_id,
        chat_type="private",
        user_id=user_id,
        message_id=42,
        text="/translate uk",
        reply_to_message=source,
    ).model_copy(update={"quote": TextQuote(text="Bonjour tout le monde.", position=position)})
    await (await test_container.get(TranslateCommand)).handle(command, recording_bot)
    settings = await test_container.get(Settings)
    namespace = await test_container.get(RuntimeNamespace)
    envelopes = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "media")
    assert len(envelopes) == expected_jobs


async def test_translate_extracts_content_only_from_rendered_bot_post(
    test_container: AsyncContainer, recording_bot: RecordingBot
) -> None:
    """Replying /translate to a bot-rendered media post translates only the post content."""
    user_id = 780402
    await (await test_container.get(ChatStore)).get_or_create_chat(user_id)
    users = await test_container.get(UserStore)
    await users.ingest_user(user_id)
    await users.set_trust(user_id, is_trusted=True, actor=ActorContext.cli())
    rendered = (
        "🗣 User shared a post by Nachez (@Nachez98)\n\n"
        "Haas y Racing Bulls votan EN CONTRA de cambiar el límite presupuestario.\n\n"
        "🔁 47 ❤️ 1194 💬 69\n"
        "🔗 Open on X"
    )
    bot_user = User(id=999999, is_bot=True, first_name="Hovorun", username="hovorun_bot")
    source = Message(
        message_id=51,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=bot_user,
        caption=rendered,
    ).as_(recording_bot)
    command = _make_command_message(
        recording_bot,
        chat_id=user_id,
        chat_type="private",
        user_id=user_id,
        message_id=52,
        text="/translate uk",
        reply_to_message=source,
    )
    await (await test_container.get(TranslateCommand)).handle(command, recording_bot)
    settings = await test_container.get(Settings)
    namespace = await test_container.get(RuntimeNamespace)
    envelopes = await asyncio.to_thread(broker_envelopes, settings, namespace.value, "media")
    assert len(envelopes) == 1
    envelope = JobEnvelope.model_validate(envelopes[0])
    assert extract_rendered_post_content(rendered) == (
        "Haas y Racing Bulls votan EN CONTRA de cambiar el límite presupuestario."
    )
    assert envelope.feature == CommandName.TRANSLATE
