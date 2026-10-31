"""Private group selection with real SQLite/Valkey and external Telegram HTTP only."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiohttp import FormData
from aioresponses import CallbackResult
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.group_management_service import GROUPS_PER_PAGE, GroupManagementService
from hovorunv2.application.identity.identity import GroupIdentityStore
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.operations.cleanup_service import CleanupService
from hovorunv2.application.ports import CachePort, ChatStore, UserStore
from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.application.translation.language_service import LanguageService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import CommandName, TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.bot import build_router
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.callbacks import SettingsMenu
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware
from hovorunv2.interface.telegram.transport.membership import TelegramMembershipAdapter
from tests.conftest import get_media_command, provision_role
from tests.utils.activation import sent_fields
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.runtimes.beta import BETA, GROUP, MEMBER, SECOND
from tests.utils.runtimes.features import feature_runtime
from tests.utils.settings import DENIED, button, query_for, settings_message
from tests.utils.telegram import API, BOT_CREDENTIAL, RecordedResponse

OTHER = GROUP - 1


@asynccontextmanager
async def private_runtime(
    container: AsyncContainer,
) -> AsyncIterator[tuple[Bot, SettingsCommand, GroupManagementService]]:
    """Compose actual management and transport adapters with synthetic credentials."""
    await feature_runtime(container)
    groups = await container.get(GroupIdentityStore)
    await groups.ingest_group(GROUP, "Managed group", "supergroup")
    await groups.ingest_group(OTHER, "Unrelated private title", "supergroup")
    async with Bot(BOT_CREDENTIAL) as bot:
        service = GroupManagementService(
            await container.get(ChatStore),
            await container.get(UserStore),
            await container.get(BanStore),
            await container.get(MembershipStore),
            TelegramMembershipAdapter(bot),
            await container.get(PermissionService),
        )
        command = SettingsCommand(
            await container.get(LanguageService),
            await container.get(CommandService),
            await container.get(AccessService),
            await container.get(CleanupService),
            await container.get(SettingsSessionService),
            service,
        )
        yield bot, command, service


async def membership_http(url: object, *, statuses: dict[int, str], **kwargs: object) -> CallbackResult:
    """Respond to exact Telegram target requests with mutable external membership facts."""
    data = kwargs["data"]
    assert isinstance(data, FormData)
    fields = {headers["name"]: value for headers, _options, value in data._fields}
    group_id, user_id = int(fields["chat_id"]), int(fields["user_id"])
    status = statuses.get(group_id, "member")
    if status == "unavailable":
        return CallbackResult(
            status=403,
            payload={"ok": False, "error_code": 403, "description": "Synthetic verification unavailable"},
            response_class=RecordedResponse,
        )
    result: dict[str, object] = {"status": status, "user": {"id": user_id, "is_bot": False, "first_name": "Synthetic"}}
    if status == "creator":
        result["is_anonymous"] = False
    return CallbackResult(payload={"ok": True, "result": result}, response_class=RecordedResponse)


def private_http(
    http: RecordedResponses, actor: int, statuses: dict[int, str], *, group_transport: bool = False
) -> None:
    """Intercept Telegram; never replace application services, repositories or cache."""
    message = settings_message(chat_id=GROUP if group_transport else actor, actor_id=actor)
    for method in ("sendMessage", "editMessageText"):
        http.post(
            f"{API}/{method}",
            payload={"ok": True, "result": message.model_dump(mode="json")},
            response_class=RecordedResponse,
            repeat=True,
        )
    for method in ("answerCallbackQuery", "deleteMessage"):
        http.post(f"{API}/{method}", payload={"ok": True, "result": True}, response_class=RecordedResponse, repeat=True)
    http.post(
        f"{API}/getChatMember",
        callback=partial(membership_http, statuses=statuses),
        response_class=RecordedResponse,
        repeat=True,
    )


def rendered_text(http: RecordedResponses) -> str:
    """Read only fields shown by the actual private settings keyboard."""
    fields = sent_fields(http, "editMessageText")[-1]
    return fields["text"] + json.dumps(json.loads(fields.get("reply_markup", "{}")), ensure_ascii=False)


async def mark_bot_absent(container: AsyncContainer, telegram_id: int) -> None:
    """Record actual LEFT bot evidence without altering durable group approval."""
    chat = await (await container.get(ChatStore)).get_chat(telegram_id)
    assert chat is not None
    now = datetime.now(UTC)
    evidence = MembershipEvidence(telegram_id, 123456, MembershipStatus.LEFT, now, now + timedelta(minutes=5))
    await (await container.get(MembershipStore)).save_bot(
        chat.id, BotCapabilityEvidence(evidence, None, None, None, None, None)
    )


@pytest.mark.slow
async def test_untrusted_admin_private_workflow_reuses_group_settings_and_audit(test_container: AsyncContainer) -> None:
    """Private group management does not grant private feature execution or change private chat settings."""
    async with private_runtime(test_container) as (bot, command, _service):
        access = await test_container.get(AccessService)
        users = await test_container.get(UserStore)
        assert not (await users.get_by_telegram_id(SECOND)).is_trusted
        settings = Settings(_env_file=None, bot_token=BOT_CREDENTIAL, cache_encryption_key="synthetic-private-test-key")
        dispatcher = Dispatcher()
        media = await get_media_command(test_container, CommandName.TWITTER)
        dispatcher.include_router(
            build_router(
                [command, media],
                MessageService(await test_container.get(CachePort), await test_container.get(ChatStore), settings),
                access,
                settings,
                await test_container.get(IdentityMiddleware),
                await test_container.get(CachePort),
            )
        )
        message = settings_message(chat_id=SECOND, actor_id=SECOND).as_(bot)
        from_user = message.from_user
        with RecordedResponses() as http:
            private_http(http, SECOND, {GROUP: "creator"})
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))
            assert "Managed group" in rendered_text(http)
            assert "Unrelated private title" not in rendered_text(http)
            selected = button(http, SettingsMenu.GROUP_SELECT, value="0")
            query = query_for(message, bot, SECOND).model_copy(update={"data": selected.pack()})
            await dispatcher.feed_update(bot, Update(update_id=2, callback_query=query))
            assert str(GROUP) in rendered_text(http)
            await command.handle_callback(query, button(http, SettingsMenu.LANG))
            await command.handle_callback(query, button(http, SettingsMenu.LANG_TARGET))
            await command.handle_callback(query, button(http, SettingsMenu.LANG_TARGET, value="pl"))
            await command.handle_callback(query, button(http, SettingsMenu.MAIN))
            await command.handle_callback(query, button(http, SettingsMenu.MODULES))
            assert "❌ Synthetic group beta" in rendered_text(http)
            await command.handle_callback(query, button(http, SettingsMenu.MODULES, value=BETA.key))
            assert "✅ Synthetic group beta" in rendered_text(http)
            private_media = message.model_copy(update={"text": "https://x.com/synthetic/status/123"})
            before = len(sent_fields(http, "editMessageText"))
            await dispatcher.feed_update(bot, Update(update_id=3, message=private_media))
            assert len(sent_fields(http, "editMessageText")) == before
        chats = await test_container.get(ChatStore)
        private_chat = await chats.get_chat(SECOND)
        assert private_chat is None or (private_chat.target_lang is None and not private_chat.features_configured)
        languages = await test_container.get(LanguageService)
        assert (await languages.get_chat_settings(GROUP))[0] == "pl"
        group_message = settings_message(actor_id=SECOND).as_(bot)
        with RecordedResponses() as http:
            private_http(http, SECOND, {GROUP: "creator"}, group_transport=True)
            await command.handle(group_message, bot)
            await command.handle_callback(query_for(group_message, bot, SECOND), button(http, SettingsMenu.MODULES))
            assert "✅ Synthetic group beta" in rendered_text(http)
        sessions = await test_container.get(async_sessionmaker)
        async with sessions() as session:
            events = (
                await session.scalars(
                    select(AuditEventDB).where(
                        AuditEventDB.action.in_(("update_translation_settings", "enable")),
                        AuditEventDB.actor_id == SECOND,
                    )
                )
            ).all()
            assert any(event.target_type == "group_beta" for event in events)
            assert any(
                event.action == "update_translation_settings" and event.target_id == str(GROUP) for event in events
            )
        assert from_user.id == SECOND


@pytest.mark.slow
@pytest.mark.parametrize("stage", ["selection", "mutation"])
@pytest.mark.parametrize("current_status", ["member", "left", "unavailable"])
async def test_demotion_rechecks_list_selection_and_mutations(
    test_container: AsyncContainer, stage: str, current_status: str
) -> None:
    """A formerly valid menu never substitutes for current Telegram administrator authority."""
    async with private_runtime(test_container) as (bot, command, _service):
        message = settings_message(chat_id=SECOND, actor_id=SECOND).as_(bot)
        statuses = {GROUP: "creator"}
        with RecordedResponses() as http:
            private_http(http, SECOND, statuses)
            await command.handle(message, bot)
            callback = button(http, SettingsMenu.GROUP_SELECT, value="0")
            query = query_for(message, bot, SECOND)
            if stage == "mutation":
                await command.handle_callback(query, callback)
                await command.handle_callback(query, button(http, SettingsMenu.LANG))
                await command.handle_callback(query, button(http, SettingsMenu.LANG_TARGET))
                callback = button(http, SettingsMenu.LANG_TARGET, value="pl")
            statuses[GROUP] = current_status
            await command.handle_callback(query, callback)
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == DENIED
        assert (await (await test_container.get(LanguageService)).get_chat_settings(GROUP))[0] != "pl"


async def test_opaque_selection_slot_and_private_actor_cannot_be_forged(test_container: AsyncContainer) -> None:
    """Changing a slot, transport actor or target cannot reveal an unrelated group."""
    async with private_runtime(test_container) as (bot, command, _service):
        message = settings_message(chat_id=SECOND, actor_id=SECOND).as_(bot)
        with RecordedResponses() as http:
            private_http(http, SECOND, {GROUP: "creator"})
            await command.handle(message, bot)
            callback = button(http, SettingsMenu.GROUP_SELECT, value="0")
            await command.handle_callback(query_for(message, bot, MEMBER), callback)
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == DENIED
            await command.handle_callback(query_for(message, bot, SECOND), callback.model_copy(update={"value": "1"}))
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == DENIED
            assert "Unrelated private title" not in rendered_text(http)
            await command.handle_callback(query_for(message, bot, SECOND), callback)
            assert str(GROUP) in rendered_text(http)


async def test_group_discovery_is_bounded_and_filters_unrelated_titles(test_container: AsyncContainer) -> None:
    """Pagination scans at most eight candidates per action and supports empty authorized pages."""
    async with private_runtime(test_container) as (_bot, _command, service):
        groups = await test_container.get(GroupIdentityStore)
        wanted = OTHER
        for offset in range(GROUPS_PER_PAGE):
            wanted = OTHER - offset - 1
            await groups.ingest_group(wanted, f"Unrelated title {offset}", "supergroup")
        with RecordedResponses() as http:
            private_http(http, SECOND, {wanted: "creator"})
            page = await service.list_groups(SECOND)
            assert not page.groups
            assert page.next_after is not None
            assert len(sent_fields(http, "getChatMember")) == GROUPS_PER_PAGE
            page = await service.list_groups(SECOND, page.next_after)
            assert [group.telegram_id for group in page.groups] == [wanted]
            assert page.next_after is None
            assert len(sent_fields(http, "getChatMember")) == GROUPS_PER_PAGE + 2


async def test_bot_admin_can_inspect_all_active_groups_but_cannot_bypass_feature_bans(
    test_container: AsyncContainer,
) -> None:
    """Private bot authority sees known groups, including restricted ones, without enabling execution."""
    async with private_runtime(test_container) as (bot, command, service):
        await provision_role(test_container, SECOND)
        await mark_bot_absent(test_container, OTHER)
        sessions = await test_container.get(async_sessionmaker)
        async with sessions() as session:
            session.add(ChatDB(chat_id=OTHER - 1, chat_type="channel", title="Not a group"))
            session.add(ChatDB(chat_id=SECOND, chat_type="private"))
            await session.commit()
        bans = await test_container.get(BanStore)
        await bans.set_ban(
            TargetKind.GROUP, GROUP, BanKind.FULL, active=True, actor=ActorContext.cli(reason="Synthetic")
        )
        page = await service.list_groups(SECOND)
        assert {group.telegram_id for group in page.groups} == {GROUP, OTHER}
        message = settings_message(chat_id=SECOND, actor_id=SECOND).as_(bot)
        with RecordedResponses() as http:
            private_http(http, SECOND, {})
            await command.handle(message, bot)
            await command.handle_callback(
                query_for(message, bot, SECOND), button(http, SettingsMenu.GROUP_SELECT, value="0")
            )
            await command.handle_callback(query_for(message, bot, SECOND), button(http, SettingsMenu.ACCESS))
            assert "Group processing is restricted" in rendered_text(http)
            assert not sent_fields(http, "getChatMember")
        access = await test_container.get(AccessService)
        assert not (await access.features.evaluate(str(CommandName.TWITTER_POST), GROUP, SECOND)).allowed


async def test_deleted_and_absent_groups_are_not_discovered_or_selected(test_container: AsyncContainer) -> None:
    """Current presence and active generations remain mandatory for ordinary group managers."""
    async with private_runtime(test_container) as (_bot, _command, service):
        chats = await test_container.get(ChatStore)
        current = await chats.get_chat(GROUP)
        assert current is not None
        target = PermissionTarget("group", current.id, current.generation, GROUP)
        assert await chats.soft_delete_chat(GROUP, ActorContext.cli(), "Synthetic lifecycle")
        await mark_bot_absent(test_container, OTHER)
        with RecordedResponses() as http:
            private_http(http, SECOND, {GROUP: "creator"})
            assert not await service.can_manage(SECOND, target)
            assert not (await service.list_groups(SECOND)).groups
            assert all(int(fields["chat_id"]) != GROUP for fields in sent_fields(http, "getChatMember"))


async def test_private_manager_can_inspect_uncertainty_without_enabling_features(
    test_container: AsyncContainer,
) -> None:
    """Current actor evidence permits inspection while missing full admin verification still blocks processing."""
    async with private_runtime(test_container) as (bot, command, _service):
        chats = await test_container.get(ChatStore)
        chat = await chats.get_chat(GROUP)
        assert chat is not None
        now = datetime.now(UTC)
        await (await test_container.get(MembershipStore)).save_admins(
            chat.id, AdministratorEvidence(GROUP, False, (), now, now + timedelta(minutes=5))
        )
        message = settings_message(chat_id=SECOND, actor_id=SECOND).as_(bot)
        with RecordedResponses() as http:
            private_http(http, SECOND, {GROUP: "creator"})
            await command.handle(message, bot)
            query = query_for(message, bot, SECOND)
            await command.handle_callback(query, button(http, SettingsMenu.GROUP_SELECT, value="0"))
            await command.handle_callback(query, button(http, SettingsMenu.ACCESS))
            assert "Administrator verification is unavailable or expired" in rendered_text(http)
        access = await test_container.get(AccessService)
        assert not (await access.features.evaluate(str(CommandName.TWITTER_POST), GROUP, SECOND)).allowed
