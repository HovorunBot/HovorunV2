"""Behavioral regression tests for B04 audited locale persistence and uk/en localization."""

import re
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User, Voice
from aiogram_i18n import I18nMiddleware
from dishka import AsyncContainer
from fluent.syntax import ast as ftl_ast
from fluent.syntax import parse as ftl_parse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.data.languages import LANGUAGES
from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.features.registry import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionLimits, AdmissionState, JobStage
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_job_runner import FeatureJobRunner
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, JobTarget, PayloadPort
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.finalization import append_translation
from hovorunv2.application.media.instagram.embed import DisabledEmbedAdapter
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.application.operations.reminder_service import ChatReminderService
from hovorunv2.application.settings.settings_session_service import SettingsTarget
from hovorunv2.application.settings.setup_drafts import (
    SetupConfigurationValues,
    SetupDraft,
    SetupStep,
)
from hovorunv2.application.speech.contracts import NoSpeech, SpeechFailure, Transcript
from hovorunv2.application.speech.policies import speech_action_key
from hovorunv2.application.speech.ports import SpeechAudioPort, SpeechSettingsStore
from hovorunv2.application.translation.explicit_input import ExplicitTranslationInput
from hovorunv2.application.translation.native_translation import NativeTranslationService
from hovorunv2.application.translation.ports import (
    CancellationToken,
    LanguageDetectorPort,
    TranslationBudget,
    TranslationEnginePort,
    TranslationScope,
)
from hovorunv2.application.translation.translation_rules import TranslationResult
from hovorunv2.application.usage.budgets import WallClock
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import WorkerQueuePublisher, WorkerRuntime
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import ChatStatus, CommandName, Role, SpeechMode, TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.runtime.feedback_gate import ValkeyFeedbackGate
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.handlers.setup import render_setup_screen
from hovorunv2.interface.telegram.handlers.speech import SpeechVoiceHandler, TranscribeCommand
from hovorunv2.interface.telegram.i18n import AudienceI18nManager, FluentTranslationCatalog
from hovorunv2.interface.telegram.middlewares import AccessMiddleware
from hovorunv2.interface.telegram.settings.callbacks import (
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)
from hovorunv2.interface.telegram.transport.speech import NativeSpeechTransport
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI
from tests.conftest import provision_role
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.settings.sessions import bind_settings_callback
from tests.utils.sync_http import HTTPFixture, json_reply
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.telegram.recording_delivery import RecordingTelegramDelivery
from tests.utils.telegram.session import RecordingBot


def _make_message(
    bot: RecordingBot,
    *,
    chat_id: int,
    chat_type: str,
    user_id: int,
    text: str | None = None,
    message_id: int = 1,
    username: str | None = None,
    reply_to_message: Message | None = None,
    voice: Voice | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type, title="B04 Group" if chat_type != "private" else None),
        from_user=User(id=user_id, is_bot=False, first_name="Tester", username=username),
        text=text,
        reply_to_message=reply_to_message,
        voice=voice,
    ).as_(bot)


async def _approve_group_with_admin(
    container: AsyncContainer,
    *,
    group_id: int,
    admin_id: int,
) -> None:
    users = await container.get(UserStore)
    await users.ingest_user(admin_id)
    await provision_role(container, admin_id)
    chats = await container.get(ChatStore)
    chat = await chats.get_or_create_chat(group_id)
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await (await container.get(GroupGrantStore)).approve(chat.id, ActorContext.cli())
    now = datetime.now(UTC) - timedelta(seconds=1)
    expiry = now + timedelta(minutes=5)
    evidence = MembershipEvidence(group_id, admin_id, MembershipStatus.ADMIN, now, expiry)
    await (await container.get(MembershipStore)).save_admins(
        chat.id,
        AdministratorEvidence(group_id, True, (evidence,), now, expiry),
        ActorContext.cli(),
    )


def _ftl_keys_and_vars(path: Path) -> dict[str, set[str]]:
    resource = ftl_parse(path.read_text(encoding="utf-8"))
    result: dict[str, set[str]] = {}
    for entry in resource.body:
        if isinstance(entry, ftl_ast.Message):
            var_names: set[str] = set()
            _collect_vars(entry.value, var_names)
            result[entry.id.name] = var_names
    return result


def _collect_vars(node: object, out: set[str]) -> None:
    if isinstance(node, ftl_ast.VariableReference):
        out.add(node.id.name)
        return
    if not isinstance(node, ftl_ast.BaseNode):
        return
    for value in vars(node).values():
        items = value if isinstance(value, list) else [value]
        for item in items:
            _collect_vars(item, out)


@pytest.mark.unit
def test_ftl_catalogs_remove_sample_keys_and_keep_parity() -> None:
    """Remove unused sample keys and maintain 1:1 key and variable parity between uk and en."""
    locales_dir = Path("src/hovorunv2/interface/telegram/locales")
    en_map = _ftl_keys_and_vars(locales_dir / "en" / "messages.ftl")
    uk_map = _ftl_keys_and_vars(locales_dir / "uk" / "messages.ftl")

    sample_keys = {"welcome", "greeting", "item-count", "status-html", "plural-files-html"}
    assert sample_keys.isdisjoint(en_map.keys())
    assert sample_keys.isdisjoint(uk_map.keys())
    assert set(en_map.keys()) == set(uk_map.keys())
    for key in en_map:
        assert en_map[key] == uk_map[key], f"Variable mismatch for {key}: en={en_map[key]} uk={uk_map[key]}"


async def test_set_chat_locale_audits_and_settings_callback_increments_version_once(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """AudienceI18nManager.set_chat_locale audits via ChatStore and /settings writes once (+1)."""
    admin_id = 740001
    group_id = -100740001
    await _approve_group_with_admin(test_container, group_id=group_id, admin_id=admin_id)

    chats = await test_container.get(ChatStore)
    i18n_manager = await test_container.get(AudienceI18nManager)
    session_maker = await test_container.get(async_sessionmaker)

    before_chat = await chats.get_chat(group_id)
    assert before_chat is not None
    initial_version = before_chat.settings_version

    await i18n_manager.set_chat_locale(group_id, "en")
    after_direct = await chats.get_chat(group_id)
    assert after_direct is not None
    assert after_direct.ui_locale == "en"
    assert after_direct.settings_version == initial_version + 1

    async with session_maker() as session:
        events = list(
            await session.scalars(
                select(AuditEventDB).where(
                    AuditEventDB.target_type == "chat",
                    AuditEventDB.target_id == str(group_id),
                    AuditEventDB.action == "set_ui_locale",
                )
            )
        )
    assert len(events) == 1

    dp = Dispatcher()
    settings_cmd = await test_container.get(SettingsCommand)
    dp.callback_query.register(settings_cmd.handle_callback, SettingsCallback.filter())
    (await test_container.get(I18nMiddleware)).setup(dp)

    msg = _make_message(recording_bot, chat_id=group_id, chat_type="supergroup", user_id=admin_id, message_id=10)
    cb = await bind_settings_callback(
        test_container,
        msg,
        admin_id,
        SettingsCallback(menu=SettingsMenu.UI_LANG, action=SettingsAction.SELECT, value="uk"),
    )
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=1,
            callback_query=CallbackQuery(
                id="cb-single-write",
                from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
                chat_instance="synth",
                message=msg,
                data=cb.pack(),
            ).as_(recording_bot),
        ),
    )

    after_callback = await chats.get_chat(group_id)
    assert after_callback is not None
    assert after_callback.ui_locale == "uk"
    assert after_callback.settings_version == initial_version + 2

    async with session_maker() as session:
        events_after = list(
            await session.scalars(
                select(AuditEventDB).where(
                    AuditEventDB.target_type == "chat",
                    AuditEventDB.target_id == str(group_id),
                    AuditEventDB.action == "set_ui_locale",
                )
            )
        )
    assert len(events_after) == 2


async def test_ukrainian_settings_submenus_render_localized_text(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """All /settings submenus render in Ukrainian when the audience locale is uk."""
    admin_id = 740010
    group_id = -100740010
    await _approve_group_with_admin(test_container, group_id=group_id, admin_id=admin_id)
    (await test_container.get(Settings)).speech_enabled = True
    i18n_manager = await test_container.get(AudienceI18nManager)
    await i18n_manager.set_chat_locale(group_id, "uk")
    await i18n_manager.set_user_locale(admin_id, "uk")

    dp = Dispatcher()
    settings_cmd = await test_container.get(SettingsCommand)
    dp.message.register(settings_cmd.handle, settings_cmd.trigger)
    dp.callback_query.register(settings_cmd.handle_callback, SettingsCallback.filter())
    (await test_container.get(I18nMiddleware)).setup(dp)

    await dp.feed_update(
        recording_bot,
        Update(
            update_id=1,
            message=_make_message(
                recording_bot, chat_id=admin_id, chat_type="private", user_id=admin_id, text="/settings", message_id=1
            ),
        ),
    )
    priv_edit = next(c for c in reversed(recording_bot.session.calls) if isinstance(c, EditMessageText))
    assert priv_edit.text is not None
    assert "Керування групами" in priv_edit.text

    group_msg = _make_message(recording_bot, chat_id=group_id, chat_type="supergroup", user_id=admin_id, message_id=20)
    menus = [
        (SettingsCallback(menu=SettingsMenu.ACCESS), "Доступ і дозволи"),  # noqa: RUF001
        (SettingsCallback(menu=SettingsMenu.USAGE, value="group"), "Використання групи"),
        (SettingsCallback(menu=SettingsMenu.NOTIFICATIONS, value="group"), "Налаштування сповіщень"),
        (SettingsCallback(menu=SettingsMenu.LANG_TARGET, page=0), "Оберіть мову перекладу"),
        (SettingsCallback(menu=SettingsMenu.MODULES), "Налаштування модулів"),
        (SettingsCallback(menu=SettingsMenu.SPEECH), "Налаштування розпізнавання мовлення"),
    ]
    for idx, (raw_cb, expected_fragment) in enumerate(menus, start=10):
        bound = await bind_settings_callback(test_container, group_msg, admin_id, raw_cb)
        await dp.feed_update(
            recording_bot,
            Update(
                update_id=idx,
                callback_query=CallbackQuery(
                    id=f"cb-menu-{idx}",
                    from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
                    chat_instance="synth",
                    message=group_msg,
                    data=bound.pack(),
                ).as_(recording_bot),
            ),
        )
        edit = next(c for c in reversed(recording_bot.session.calls) if isinstance(c, EditMessageText))
        assert edit.text is not None
        assert expected_fragment in edit.text
        assert isinstance(edit.reply_markup, InlineKeyboardMarkup)
        btn_texts = [btn.text for row in edit.reply_markup.inline_keyboard for btn in row]
        assert any("Назад" in t for t in btn_texts)


@pytest.mark.unit
def test_setup_speech_step_renders_ukrainian_options() -> None:
    """SetupStep.SPEECH renders localized speech option labels."""
    values = SetupConfigurationValues(
        ui_locale="uk",
        target_lang="uk",
        speech_mode=SpeechMode.MANUAL_ONLY,
    )
    draft = SetupDraft(
        session_id="sess1",
        target=SettingsTarget(entity_id=1, generation=1, telegram_id=-1001),
        owner_id=1001,
        owner_generation=1,
        transport_chat_id=-1001,
        message_id=1,
        step=SetupStep.SPEECH,
        baseline=values,
        draft=values,
        operation_id="op1",
        created_at=1000.0,
        inactivity_expires_at=9999999999.0,
        absolute_expires_at=9999999999.0,
    )
    text, markup = render_setup_screen(draft, "uk")
    btn_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "Вимкнено" in btn_texts
    assert "✅ Лише вручну" in btn_texts
    assert "Вручну + автоматично" in btn_texts
    assert "Лише вручну" in text


@pytest.mark.unit
def test_append_translation_supports_localized_label() -> None:
    """append_translation renders the provided localized label."""
    translated = TranslationResult(text="Привіт", target_lang="uk", flag="🇬🇧")
    assert append_translation("Hello", translated, label="Переклад:") == (
        "Hello\n\n🇬🇧 <b>Переклад:</b>\nПривіт"  # noqa: RUF001
    )


class _StubSpeechAudio(SpeechAudioPort):
    def fetch_metadata(self, file_id: str, deadline: float, *, cancelled: object) -> dict[str, object]:
        return {"file_path": "voice/test.ogg", "file_size": 100}

    def fetch_file(self, file_path: str, deadline: float, *, cancelled: object) -> bytes:
        return b"ogg"


@pytest.mark.unit
def test_native_speech_transport_localizes_uk_and_en() -> None:
    """NativeSpeechTransport renders transcript header, no-speech, and failure notices in uk and en."""
    routes = {f"/bot{BOT_CREDENTIAL}/sendMessage": json_reply({"ok": True, "result": {"message_id": 1}})}
    with (
        HTTPFixture(routes) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        api = NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url(""))
        transport = NativeSpeechTransport(api, _StubSpeechAudio())

        transport.deliver_speech(-1001, 10, None, Transcript(text="Привіт", language="uk"), ui_locale="uk")
        uk_text = parse_qs(server.requests[-1].body.decode())["text"][0]
        assert "<b>Транскрипт</b> (uk):" in uk_text

        transport.deliver_speech(-1001, 10, None, NoSpeech(), ui_locale="uk")
        assert "Мовлення не виявлено" in parse_qs(server.requests[-1].body.decode())["text"][0]

        transport.deliver_speech(-1001, 10, None, SpeechFailure.DECODE_FAILED, ui_locale="uk")
        assert "не вдалося декодувати" in parse_qs(server.requests[-1].body.decode())["text"][0].lower()

        transport.deliver_speech(-1001, 10, None, Transcript(text="Hello", language="en"), ui_locale="en")
        en_text = parse_qs(server.requests[-1].body.decode())["text"][0]
        assert "<b>Transcript</b> (en):" in en_text


async def test_transcribe_ukrainian_and_automatic_voice_duplicate_silent(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """/transcribe replies in Ukrainian for uk chats; automatic voice triggers stay silent on DUPLICATE."""
    admin_id = 740020
    group_id = -100740020
    await _approve_group_with_admin(test_container, group_id=group_id, admin_id=admin_id)
    (await test_container.get(Settings)).speech_enabled = True
    i18n_manager = await test_container.get(AudienceI18nManager)
    await i18n_manager.set_chat_locale(group_id, "uk")

    dp = Dispatcher()
    transcribe_cmd = await test_container.get(TranscribeCommand)
    voice_handler = await test_container.get(SpeechVoiceHandler)
    dp.message.register(transcribe_cmd.handle, transcribe_cmd.trigger)
    dp.message.register(voice_handler.handle, voice_handler.trigger)
    (await test_container.get(I18nMiddleware)).setup(dp)

    await dp.feed_update(
        recording_bot,
        Update(
            update_id=1,
            message=_make_message(
                recording_bot,
                chat_id=group_id,
                chat_type="supergroup",
                user_id=admin_id,
                text="/transcribe",
                message_id=1,
            ),
        ),
    )
    reply_msg = next(c for c in reversed(recording_bot.session.calls) if isinstance(c, SendMessage))
    assert "Дайте відповідь на голосове або аудіоповідомлення" in reply_msg.text  # noqa: RUF001

    chats = await test_container.get(ChatStore)
    chat = await chats.get_chat(group_id)
    assert chat is not None
    speech_store = await test_container.get(SpeechSettingsStore)
    await speech_store.set_mode(
        PermissionTarget("group", chat.id, chat.generation, chat.chat_id),
        SpeechMode.MANUAL_AND_AUTOMATIC,
        actor=ActorContext.telegram(admin_id),
        transport_kind="group",
    )

    admission = await test_container.get(AdmissionService)
    payloads = await test_container.get(PayloadPort)
    now = WallClock().now()
    sealed = await payloads.seal("pre-existing-speech", b"{}", now + 60.0)
    dedupe_key = speech_action_key(group_id, 55, chat.id, chat.generation)
    admit_res = await admission.admit(
        JobEnvelope(
            job_id="pre-existing-speech",
            payload_ref=sealed.reference,
            feature=CommandName.TRANSCRIBE_AUTO,
            target=JobTarget(
                kind=TargetKind.GROUP, entity_id=chat.id, generation=chat.generation, telegram_id=group_id
            ),
            actor_id=admin_id,
            actor_generation=1,
            trigger=TriggerKind.AUTOMATIC,
            charged_kind=TargetKind.GROUP,
            charged_telegram_id=group_id,
            accepted_at=now,
            deadline=now + 60.0,
            dedupe_key=dedupe_key,
            feature_epoch=0,
            message_id=55,
            ui_locale="uk",
        ),
        sealed,
    )
    assert admit_res.state == AdmissionState.ACCEPTED

    calls_before = len(recording_bot.session.calls)
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=2,
            message=_make_message(
                recording_bot,
                chat_id=group_id,
                chat_type="supergroup",
                user_id=admin_id,
                message_id=55,
                voice=Voice(file_id="voice-file-1", file_unique_id="uniq-1", duration=5, file_size=1024),
            ),
        ),
    )
    new_sends = [c for c in recording_bot.session.calls[calls_before:] if isinstance(c, SendMessage)]
    assert new_sends == []


async def test_access_creator_dms_localized_per_recipient(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """AccessCommand sends approval DMs and edits prompts in each recipient's personal locale."""
    uk_owner_id = 740030
    en_admin_id = 740031
    requester_id = 740032
    group_id = -100740030

    await provision_role(test_container, uk_owner_id, Role.OWNER)
    await provision_role(test_container, en_admin_id, Role.ADMIN)
    users = await test_container.get(UserStore)
    await users.ingest_user(requester_id)
    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(group_id)
    now = datetime.now(UTC) - timedelta(seconds=1)
    expiry = now + timedelta(minutes=5)
    evidence = MembershipEvidence(group_id, requester_id, MembershipStatus.ADMIN, now, expiry)
    await (await test_container.get(MembershipStore)).save_admins(
        chat.id,
        AdministratorEvidence(group_id, True, (evidence,), now, expiry),
        ActorContext.cli(),
    )

    i18n_manager = await test_container.get(AudienceI18nManager)
    await i18n_manager.set_user_locale(uk_owner_id, "uk")
    await i18n_manager.set_user_locale(en_admin_id, "en")
    await i18n_manager.set_chat_locale(group_id, "uk")

    dp = Dispatcher()
    access_cmd = await test_container.get(AccessCommand)
    dp.message.register(access_cmd.handle, access_cmd.trigger)
    access_cmd.register_callbacks(dp, {})
    (await test_container.get(I18nMiddleware)).setup(dp)

    with RecordedResponses() as http_mock:
        http_mock.post(
            re.compile(r"https://api\.telegram\.org/bot[^/]+/getChatMember"),
            payload={
                "ok": True,
                "result": {
                    "status": "creator",
                    "user": {"id": requester_id, "is_bot": False, "first_name": "Req"},
                    "is_anonymous": False,
                },
            },
        )
        await dp.feed_update(
            recording_bot,
            Update(
                update_id=1,
                message=_make_message(
                    recording_bot,
                    chat_id=group_id,
                    chat_type="supergroup",
                    user_id=requester_id,
                    text="/allow_chat",
                    message_id=1,
                    username="req_user",
                ),
            ),
        )

    uk_dm = next(c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == uk_owner_id)
    en_dm = next(c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == en_admin_id)
    assert "Запит на доступ" in uk_dm.text
    assert "Access Request" in en_dm.text

    assert isinstance(en_dm.reply_markup, InlineKeyboardMarkup)
    approve_btn = en_dm.reply_markup.inline_keyboard[0][0]
    assert approve_btn.callback_data is not None
    prompt_msg = _make_message(
        recording_bot, chat_id=en_admin_id, chat_type="private", user_id=en_admin_id, message_id=99
    )
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=2,
            callback_query=CallbackQuery(
                id="cb-approve",
                from_user=User(id=en_admin_id, is_bot=False, first_name="EnAdmin"),
                chat_instance="synth",
                message=prompt_msg,
                data=approve_btn.callback_data,
            ).as_(recording_bot),
        ),
    )
    edited = next(c for c in reversed(recording_bot.session.calls) if isinstance(c, EditMessageText))
    assert edited.text is not None
    assert "Group approval saved" in edited.text


async def test_access_middleware_denials_localized_in_ukrainian(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """AccessMiddleware renders callback denial alerts in the actor's personal locale."""
    uk_user_id = 740035
    unapproved_group_id = -100740035
    users = await test_container.get(UserStore)
    await users.ingest_user(uk_user_id)
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(unapproved_group_id)
    i18n_manager = await test_container.get(AudienceI18nManager)
    await i18n_manager.set_user_locale(uk_user_id, "uk")

    middleware = AccessMiddleware(
        access_service=await test_container.get(AccessService),
        cache=await test_container.get(CachePort),
        settings=await test_container.get(AppSettings),
        reminders=await test_container.get(ChatReminderService),
        i18n_manager=i18n_manager,
    )

    async def _noop_handler(event: object, data: object) -> None:
        return None

    group_msg = _make_message(
        recording_bot, chat_id=unapproved_group_id, chat_type="supergroup", user_id=uk_user_id, message_id=1
    )
    cb = CallbackQuery(
        id="cb-unapproved",
        from_user=User(id=uk_user_id, is_bot=False, first_name="UkUser"),
        chat_instance="synth",
        message=group_msg,
        data="noop",
    ).as_(recording_bot)
    await middleware(_noop_handler, cb, {"bot": recording_bot})
    ans = next(c for c in reversed(recording_bot.session.calls) if isinstance(c, AnswerCallbackQuery))
    assert ans.text is not None
    assert "Доступ заборонено" in ans.text


class _StaticFrenchDetector(LanguageDetectorPort):
    def detect(self, text: str) -> str:
        return "fr"


class _NeverCalledEngine(TranslationEnginePort):
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        *,
        budget: TranslationBudget | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> str:
        msg = "Engine should not be called on same-language source"
        raise AssertionError(msg)


async def test_explicit_same_language_and_worker_notices_localized(
    test_container: AsyncContainer,
) -> None:
    """Explicit same-language resolves chat target_lang display name and worker notices localize via ui_locale."""
    settings = await test_container.get(Settings)
    group_id = -100740040
    user_id = 740040

    users = await test_container.get(UserStore)
    user = await users.ingest_user(user_id)
    assert user is not None
    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(group_id)
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await chats.update_translation_settings(group_id, "fr", [])
    await (await test_container.get(CommandStore)).initialize_defaults(group_id, (CommandName.TRANSLATE,))
    await (await test_container.get(GroupGrantStore)).approve(chat.id, ActorContext.cli())
    now_dt = datetime.now(UTC) - timedelta(seconds=1)
    await (await test_container.get(MembershipStore)).save_admins(
        chat.id,
        AdministratorEvidence(
            group_id,
            True,
            (MembershipEvidence(group_id, user_id, MembershipStatus.ADMIN, now_dt, now_dt + timedelta(minutes=5)),),
            now_dt,
            now_dt + timedelta(minutes=5),
        ),
        ActorContext.cli(),
    )

    clock = ManualClock(time.time())
    safety = UsageSafetyCaps()
    limits = AdmissionLimits(10, 10, safety)
    routes = {
        f"/bot{BOT_CREDENTIAL}/sendMessage": json_reply({"ok": True, "result": {"message_id": 100}}),
    }
    catalog = FluentTranslationCatalog()
    async with runtime_cache(test_container) as (cache, namespace):
        payloads = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        admission = ValkeyAdmissionStore(cache, namespace)
        now = clock.now()
        exp_input = ExplicitTranslationInput(
            text="Bonjour tout le monde",
            source_chat_id=group_id,
            source_message_id=10,
            reply_to_message_id=10,
            scope=TranslationScope.FULL_SOURCE,
            ui_locale="en",
            requested_target_lang=None,
        )
        sealed = await payloads.seal("b04-same-lang", exp_input.model_dump_json().encode("utf-8"), now + 60.0)
        envelope = JobEnvelope(
            job_id="b04-same-lang",
            payload_ref=sealed.reference,
            feature=CommandName.TRANSLATE,
            target=JobTarget(
                kind=TargetKind.GROUP, entity_id=chat.id, generation=chat.generation, telegram_id=group_id
            ),
            actor_id=user_id,
            actor_generation=user.generation,
            trigger=TriggerKind.DIRECT,
            charged_kind=TargetKind.USER,
            charged_telegram_id=user_id,
            accepted_at=now,
            deadline=now + 60.0,
            dedupe_key="a" * 64,
            feature_epoch=0,
            message_id=10,
            ui_locale="en",
        )
        assert (await admission.admit(envelope, sealed, limits, now)).state == AdmissionState.ACCEPTED

        expired_sealed = await payloads.seal("b04-expired-uk", exp_input.model_dump_json().encode("utf-8"), now + 10.0)
        expired_env = envelope.model_copy(
            update={
                "job_id": "b04-expired-uk",
                "payload_ref": expired_sealed.reference,
                "dedupe_key": "b" * 64,
                "accepted_at": now,
                "deadline": now + 10.0,
                "ui_locale": "uk",
            }
        )
        assert (await admission.admit(expired_env, expired_sealed, limits, now)).state == AdmissionState.ACCEPTED

        with (
            HTTPFixture(routes) as server,
            NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
            WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
            closing(WorkerQueuePublisher(settings.valkey_url, namespace, safety, clock)) as publisher,
        ):
            translation = NativeTranslationService(
                settings=settings,
                preferences=database.policies,
                detector=_StaticFrenchDetector(),
                engine=_NeverCalledEngine(),
            )
            delivery = RecordingTelegramDelivery()
            runner = FeatureJobRunner(
                settings=settings,
                admission=runtime.admission,
                payloads=runtime.payloads,
                budgets=runtime.budgets,
                policies=database.policies,
                ledger=database.ledger,
                http=http,
                telegram=NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url("")),
                translation=translation,
                extractor=NativeMediaExtractor(),
                feedback=database.policies,
                gate=ValkeyFeedbackGate(runtime.client, namespace),
                embed=DisabledEmbedAdapter(),
                clock=clock,
                safety=safety,
                publisher=publisher,
                delivery=delivery,
                catalog=catalog,
            )

            runner.run(envelope.model_dump(mode="json"), JobStage.MEDIA)
            same_lang_text = parse_qs(server.requests[-1].body.decode())["text"][0]
            assert LANGUAGES["fr"].display_name in same_lang_text

            clock.value += 20.0
            runner.run(expired_env.model_dump(mode="json"), JobStage.MEDIA)
            expired_text = parse_qs(server.requests[-1].body.decode())["text"][0]
            assert "час очікування" in expired_text.lower() or "вичерпано" in expired_text.lower()
