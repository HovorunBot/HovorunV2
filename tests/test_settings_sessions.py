"""Settings target, lifecycle, replay and authority checks with real SQLite and Valkey."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, User
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.group_migration import GroupMigrationStore
from hovorunv2.application.ports import CachePort, ChatStore, UserStore
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.application.services.settings_session_service import SETTINGS_SESSION_TTL, SettingsSessionService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.telegram.callbacks import SettingsAction, SettingsCallback, SettingsMenu
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from tests.conftest import provision_role
from tests.test_group_beta import FIRST, GROUP, SECOND, group_beta_runtime
from tests.test_membership_adapter import API, BOT_CREDENTIAL, RecordedResponse
from tests.utils.activation import sent_fields
from tests.utils.postgres import reject_audit
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.settings import bind_settings_callback

DENIED = "Settings unavailable. Reopen /settings."


def settings_message(chat_id: int = GROUP, actor_id: int = FIRST, message_id: int = 31) -> Message:
    """Supply real transport facts independently of callback arguments."""
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
        from_user=User(id=actor_id, is_bot=False, first_name="Synthetic"),
        text="/settings",
    )


def query_for(message: Message, bot: Bot, actor_id: int = FIRST) -> CallbackQuery:
    """Bind callback and nested message to the external Telegram client."""
    return CallbackQuery(
        id="synthetic-settings",
        chat_instance="synthetic",
        from_user=User(id=actor_id, is_bot=False, first_name="Synthetic"),
        message=message.as_(bot),
    ).as_(bot)


def settings_http(http: RecordedResponses, message: Message, *, admin: bool = True) -> None:
    """Intercept external Telegram calls only, including fresh membership queries."""
    for method in ("sendMessage", "editMessageText"):
        http.post(
            f"{API}/{method}",
            payload={"ok": True, "result": message.model_dump(mode="json")},
            repeat=True,
            response_class=RecordedResponse,
        )
    for method in ("answerCallbackQuery", "deleteMessage"):
        http.post(f"{API}/{method}", payload={"ok": True, "result": True}, repeat=True, response_class=RecordedResponse)
    member = {"status": "creator" if admin else "member", "user": message.from_user.model_dump(mode="json")}
    if admin:
        member["is_anonymous"] = False
    http.post(
        f"{API}/getChatMember", payload={"ok": True, "result": member}, repeat=True, response_class=RecordedResponse
    )


def button(http: RecordedResponses, menu: SettingsMenu, *, value: str | None = None) -> SettingsCallback:
    """Read the real server-rendered callback rather than fabricate trusted state."""
    markup = json.loads(sent_fields(http, "editMessageText")[-1]["reply_markup"])
    for row in markup["inline_keyboard"]:
        for item in row:
            if not item["callback_data"].startswith("settings:"):
                continue
            callback = SettingsCallback.unpack(item["callback_data"])
            assert len(callback.pack().encode()) <= 64  # Telegram callback byte limit.
            if callback.menu == menu and (value is None or callback.value == value):
                return callback
    raise AssertionError(menu)


async def test_real_settings_language_workflow_and_transactional_actor_audit(test_container: AsyncContainer) -> None:
    """Navigate actual menus; selection and audit commit together, and close consumes its token."""
    await group_beta_runtime(test_container)
    command = await test_container.get(SettingsCommand)
    message = settings_message()
    cleanup = await test_container.get(CleanupService)
    await cleanup.register_window(GROUP, message.message_id - 1, SETTINGS_SESSION_TTL, CommandName.SETTINGS)
    async with Bot(BOT_CREDENTIAL) as bot:
        with RecordedResponses() as http:
            settings_http(http, message)
            await command.handle(message.as_(bot), bot)
            assert [int(fields["message_id"]) for fields in sent_fields(http, "deleteMessage")] == [
                message.message_id - 1
            ]
            query = query_for(message, bot)
            old = button(http, SettingsMenu.LANG)
            await command.handle_callback(query, old)
            await command.handle_callback(query, button(http, SettingsMenu.LANG_TARGET))
            await command.handle_callback(query, button(http, SettingsMenu.LANG_TARGET, value="pl"))
            language = await test_container.get(LanguageService)
            target, _ = await language.get_chat_settings(GROUP)
            assert target == "pl"
            await command.handle_callback(query, button(http, SettingsMenu.LANG_IGNORED))
            await command.handle_callback(query, button(http, SettingsMenu.LANG_IGNORED, value="de"))
            _, ignored = await language.get_chat_settings(GROUP)
            assert "de" in ignored
            assert "und" in ignored
            await command.handle_callback(query, old)
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == DENIED
            await command.handle_callback(query, button(http, SettingsMenu.LANG))
            await command.handle_callback(query, button(http, SettingsMenu.MAIN))
            close = button(http, SettingsMenu.CLOSE)
            before_close = len(sent_fields(http, "deleteMessage"))
            await command.handle_callback(query, close)
            assert len(sent_fields(http, "deleteMessage")) == before_close + 1
            await command.handle_callback(query, close)
            assert len(sent_fields(http, "deleteMessage")) == before_close + 1
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        events = (
            await session.scalars(select(AuditEventDB).where(AuditEventDB.action == "update_translation_settings"))
        ).all()
        assert len(events) == 2
        assert all(event.actor_id == FIRST and event.target_id == str(GROUP) for event in events)


@pytest.mark.parametrize("tamper", ["group", "message", "menu", "value", "page", "token"])
async def test_callback_tampering_changes_no_durable_state(test_container: AsyncContainer, tamper: str) -> None:
    """A valid token cannot authorize another target, window or operation."""
    await group_beta_runtime(test_container)
    command = await test_container.get(SettingsCommand)
    message = settings_message()
    callback = await bind_settings_callback(
        test_container,
        message,
        FIRST,
        SettingsCallback(menu=SettingsMenu.LANG_TARGET, action=SettingsAction.SELECT, value="pl"),
    )
    match tamper:
        case "group":
            message = settings_message(chat_id=GROUP - 1)
        case "message":
            message = settings_message(message_id=message.message_id + 1)
        case "menu":
            callback.menu = SettingsMenu.LANG_IGNORED
        case "value":
            callback.value = "de"
        case "page":
            callback.page = 100
        case "token":
            callback.token = "guessed"  # noqa: S105 - Deliberately forged UI token.
    async with Bot(BOT_CREDENTIAL) as bot:
        with RecordedResponses() as http:
            settings_http(http, message)
            await command.handle_callback(query_for(message, bot), callback)
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == DENIED
            assert not sent_fields(http, "editMessageText")
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert not (
            await session.scalars(select(AuditEventDB).where(AuditEventDB.action == "update_translation_settings"))
        ).all()


async def test_demoted_admin_cannot_mutate_an_older_menu(test_container: AsyncContainer) -> None:
    """Fresh Telegram membership overrides the old persisted administrator snapshot."""
    await group_beta_runtime(test_container)
    command = await test_container.get(SettingsCommand)
    message = settings_message()
    callback = await bind_settings_callback(
        test_container,
        message,
        FIRST,
        SettingsCallback(menu=SettingsMenu.LANG_TARGET, action=SettingsAction.SELECT, value="pl"),
    )
    async with Bot(BOT_CREDENTIAL) as bot:
        with RecordedResponses() as http:
            settings_http(http, message, admin=False)
            await command.handle_callback(query_for(message, bot), callback)
            assert len(sent_fields(http, "getChatMember")) == 1
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == DENIED
    languages = await test_container.get(LanguageService)
    assert (await languages.get_chat_settings(GROUP))[0] != "pl"


async def test_private_context_uses_selected_group_and_denies_other_actor(test_container: AsyncContainer) -> None:
    """Private transport cannot replace the selected target or transfer its authority token."""
    await group_beta_runtime(test_container)
    await provision_role(test_container, FIRST)
    command = await test_container.get(SettingsCommand)
    message = settings_message(chat_id=FIRST)
    callback = await bind_settings_callback(
        test_container,
        message,
        FIRST,
        SettingsCallback(menu=SettingsMenu.LANG_TARGET, action=SettingsAction.SELECT, value="pl"),
        target=GROUP,
    )
    async with Bot(BOT_CREDENTIAL) as bot:
        with RecordedResponses() as http:
            settings_http(http, message)
            await command.handle_callback(query_for(message, bot, SECOND), callback)
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == DENIED
            await command.handle_callback(query_for(message, bot), callback)
            assert (await (await test_container.get(LanguageService)).get_chat_settings(GROUP))[0] == "pl"
            chats = await test_container.get(ChatStore)
            assert await chats.get_chat(FIRST) is None
            await command.handle_callback(query_for(message, bot), callback)
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == DENIED


async def test_session_single_use_is_atomic_and_replacement_invalidates_old_window(
    test_container: AsyncContainer,
) -> None:
    """Real Valkey GETDEL admits one concurrent consumer and rejects the replaced window."""
    await group_beta_runtime(test_container)
    service = await test_container.get(SettingsSessionService)
    context = await service.prepare(GROUP, FIRST, GROUP, 31)
    token = await service.issue(context, ["language-select"])
    results = await asyncio.gather(
        *(service.resolve(token, "language-select", FIRST, GROUP, 31, consume=True) for _ in range(2))
    )
    assert sum(result is not None for result in results) == 1
    old = await service.issue(context, ["language-select"])
    current = await service.issue(context.model_copy(update={"message_id": 32}), ["language-select"])
    assert await service.resolve(old, "language-select", FIRST, GROUP, 31) is None
    assert await service.resolve(current, "language-select", FIRST, GROUP, 32) is not None


async def test_expiry_checks_absolute_deadline_and_sets_bounded_cache(test_container: AsyncContainer) -> None:
    """Clock-controlled expired context is rejected even when its cache entry still exists."""
    await group_beta_runtime(test_container)
    service = await test_container.get(SettingsSessionService)
    cache = await test_container.get(CachePort)
    context = await service.prepare(GROUP, FIRST, GROUP, 31)
    token = await service.issue(context, ["select"])
    raw = await cache.get(f"settings:session:{token}")
    assert raw["expires_at"] == context.expires_at
    raw["expires_at"] = 0.0
    await cache.set(f"settings:session:{token}", raw, expire=30)
    assert await service.resolve(token, "select", FIRST, GROUP, 31) is None
    with pytest.raises(ValueError, match="expired"):
        await service.issue(context.model_copy(update={"expires_at": 0.0}), ["select"])


@pytest.mark.parametrize("change", ["migration", "generation", "actor_deletion"])
async def test_lifecycle_and_routing_changes_invalidate_session(test_container: AsyncContainer, change: str) -> None:
    """Old tokens cannot follow aliases, new group generations or deleted private actors."""
    await group_beta_runtime(test_container)
    service = await test_container.get(SettingsSessionService)
    context = await service.prepare(GROUP, FIRST, FIRST, 31)
    token = await service.issue(context, ["select"])
    if change == "migration":
        migration = await test_container.get(GroupMigrationStore)
        assert await migration.migrate(GROUP, GROUP - 1)
    elif change == "generation":
        chats = await test_container.get(ChatStore)
        assert await chats.soft_delete_chat(GROUP, ActorContext.cli(), "Synthetic lifecycle test")
        sessions = await test_container.get(async_sessionmaker)
        async with sessions() as session:
            session.add(ChatDB(chat_id=GROUP, generation=2))
            await session.commit()
    else:
        users = await test_container.get(UserStore)
        assert await users.soft_delete_user(FIRST, ActorContext.cli(), "Synthetic lifecycle test")
    assert await service.resolve(token, "select", FIRST, FIRST, 31) is None


async def test_anonymous_sender_cannot_open_user_bound_settings(test_container: AsyncContainer) -> None:
    """Anonymous sender-chat identity cannot borrow the apparent from_user's grants."""
    await group_beta_runtime(test_container)
    command = await test_container.get(SettingsCommand)
    message = settings_message().model_copy(update={"sender_chat": Chat(id=GROUP, type="supergroup")})
    async with Bot(BOT_CREDENTIAL) as bot:
        with RecordedResponses() as http:
            settings_http(http, message)
            await command.handle(message.as_(bot), bot)
            assert not http.requests


async def test_language_audit_failure_rolls_back_settings(test_container: AsyncContainer) -> None:
    """A failing audit insert must never leave the language mutation committed."""
    await group_beta_runtime(test_container)
    languages = await test_container.get(LanguageService)
    original = await languages.get_chat_settings(GROUP)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        await reject_audit(
            session, "reject_language_audit", "NEW.action = 'update_translation_settings'", "synthetic audit failure"
        )
        await session.commit()
    with pytest.raises(IntegrityError, match="synthetic audit failure"):
        await languages.update_settings(GROUP, "pl", ["de"], actor=ActorContext.telegram(FIRST))
    assert await languages.get_chat_settings(GROUP) == original
