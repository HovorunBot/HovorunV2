"""Verify expired administrator recovery through real settings and media routing."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import EditMessageText, GetChatAdministrators, SendMessage
from aiogram.types import Update
from dishka import AsyncContainer, Provider, Scope, make_async_container
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.ban_service import BanService
from hovorunv2.application.access.permissions import DecisionReason
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import AdministratorEvidence
from hovorunv2.application.identity.membership_reconciliation import MembershipReconciliationService
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionPort, TicketState
from hovorunv2.application.jobs.feature_jobs import JobEnvelope
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.application.operations.reminder_service import ChatReminderService
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.bootstrap.container import AppProvider
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import ChatStatus, CommandName, TargetKind
from hovorunv2.domain.membership import GroupMembershipStateDB
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.bot import build_router
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.callbacks import SettingsCallback, SettingsMenu
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware
from hovorunv2.interface.telegram.transport.membership import TelegramMembershipAdapter
from tests.conftest import TestInfrastructureProvider as RecoveryInfrastructureProvider
from tests.conftest import get_media_command
from tests.utils.jobs import broker_envelopes
from tests.utils.runtime import runtime_cache
from tests.utils.settings import query_for, settings_message
from tests.utils.telegram import GROUP_ID, USER_ID, member_payload

MEDIA_URL = "https://www.instagram.com/p/Synthetic_1/"


@pytest.fixture
async def recovery_container(test_container: AsyncContainer, test_engine: AsyncEngine) -> AsyncIterator[AsyncContainer]:
    """Isolate actual admission and broker state without replacing any application service."""
    async with runtime_cache(test_container) as (_cache, namespace):
        overrides = Provider(scope=Scope.APP)
        overrides.from_context(RuntimeNamespace, override=True)
        container = make_async_container(
            RecoveryInfrastructureProvider(test_engine), AppProvider(), overrides, context={RuntimeNamespace: namespace}
        )
        try:
            yield container
        finally:
            await container.close()


async def _expire_administrators(container: AsyncContainer) -> int:
    """Persist an expired known snapshot for an approved group and return its internal ID."""
    group = await (await container.get(ChatStore)).get_or_create_chat(GROUP_ID)
    statuses = await container.get(ChatStatusService)
    await statuses.approve(GROUP_ID)
    stale = datetime.now(UTC) - timedelta(minutes=6)
    await (await container.get(MembershipStore)).save_admins(
        group.id, AdministratorEvidence(GROUP_ID, True, (), stale, stale + timedelta(minutes=5))
    )
    # Expire an existing fresh snapshot without bypassing monotonic evidence ingestion.
    sessions = await container.get(async_sessionmaker)
    async with sessions() as session, session.begin():
        await session.execute(
            update(GroupMembershipStateDB)
            .where(GroupMembershipStateDB.group_id == group.id)
            .values(admins_verified_at=stale, admins_expires_at=stale + timedelta(minutes=5))
        )
    assert (await statuses.evaluate_access(GROUP_ID)).reason == DecisionReason.MEMBER_EVIDENCE_UNKNOWN
    return group.id


async def _dispatcher(container: AsyncContainer, bot: Bot, command: BaseCommand) -> Dispatcher:
    """Compose production routing and persistence with recording Telegram verification."""
    cache = await container.get(CachePort)
    reconciliation = MembershipReconciliationService(
        await container.get(MembershipStore),
        TelegramMembershipAdapter(bot),
        await container.get(ChatStore),
        cache,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(
            [command],
            await container.get(MessageService),
            await container.get(AccessService),
            await container.get(Settings),
            await container.get(IdentityMiddleware),
            cache,
            await container.get(ChatReminderService),
            reconciliation,
        )
    )
    return dispatcher


def _record_successful_refresh(bot: Bot) -> None:
    """Supply successful external bot, administrator-list and administrator-member responses."""
    bot.session.responses.extend(
        [member_payload("creator", is_bot=True), [member_payload("creator")], member_payload("creator")]
    )


def _settings_callback(render: EditMessageText, menu: SettingsMenu) -> SettingsCallback:
    """Select the actual session-bound action shown in the rendered settings keyboard."""
    assert render.reply_markup is not None
    for row in render.reply_markup.inline_keyboard:
        for button in row:
            if not button.callback_data:
                continue
            callback = SettingsCallback.unpack(button.callback_data)
            if callback.menu == menu:
                return callback
    msg = f"Settings did not render the {menu} action"
    raise AssertionError(msg)


@pytest.mark.parametrize("chat_type", [ChatType.GROUP, ChatType.SUPERGROUP])
async def test_expired_settings_command_and_callback_recover_on_the_triggering_request(
    recovery_container: AsyncContainer, recording_bot: Bot, chat_type: ChatType
) -> None:
    """Open real settings and its bound language menu despite separately expired administrator snapshots."""
    await _expire_administrators(recovery_container)
    command = await recovery_container.get(SettingsCommand)
    dispatcher = await _dispatcher(recovery_container, recording_bot, command)
    message = settings_message(chat_id=GROUP_ID, actor_id=USER_ID)
    message = message.model_copy(update={"chat": message.chat.model_copy(update={"type": chat_type})})
    window = message.model_copy(update={"message_id": message.message_id + 1})
    _record_successful_refresh(recording_bot)
    recording_bot.session.responses.extend(
        [member_payload("creator"), member_payload("creator"), window.model_dump(mode="json"), True]
    )

    await dispatcher.feed_update(recording_bot, Update(update_id=1, message=message))

    assert any(isinstance(call, SendMessage) for call in recording_bot.session.calls)
    renders = [call for call in recording_bot.session.calls if isinstance(call, EditMessageText)]
    assert len(renders) == 1
    callback = _settings_callback(renders[0], SettingsMenu.LANG)
    assert (await (await recovery_container.get(ChatStatusService)).evaluate_access(GROUP_ID)).allowed

    await _expire_administrators(recovery_container)
    _record_successful_refresh(recording_bot)
    recording_bot.session.responses.extend([member_payload("creator"), member_payload("creator"), True])
    query = query_for(window, recording_bot, USER_ID).model_copy(update={"data": callback.pack()})
    await dispatcher.feed_update(recording_bot, Update(update_id=2, callback_query=query))

    renders = [call for call in recording_bot.session.calls if isinstance(call, EditMessageText)]
    assert len(renders) == 2
    assert _settings_callback(renders[-1], SettingsMenu.LANG_TARGET).token
    assert (await (await recovery_container.get(ChatStatusService)).evaluate_access(GROUP_ID)).allowed


@pytest.mark.parametrize("restriction", ["none", "group_ban", "admin_ban", "unselected"])
async def test_expired_media_evidence_recovers_without_bypassing_restrictions(
    recovery_container: AsyncContainer, recording_bot: Bot, restriction: str
) -> None:
    """Publish the triggering Instagram job only when refreshed evidence and existing policy both allow it."""
    await _expire_administrators(recovery_container)
    await (await recovery_container.get(UserStore)).ingest_user(USER_ID)
    bans = await recovery_container.get(BanService)
    if restriction == "group_ban":
        await bans.apply(TargetKind.GROUP, GROUP_ID, BanKind.FULL, ActorContext.cli(reason="Synthetic access check"))
    if restriction == "admin_ban":
        await bans.apply(TargetKind.USER, USER_ID, BanKind.FULL, ActorContext.cli(reason="Synthetic access check"))
    if restriction == "unselected":
        await (await recovery_container.get(CommandService)).disable_command(
            GROUP_ID, CommandName.INSTAGRAM, actor=ActorContext.cli()
        )
    command = await get_media_command(recovery_container, CommandName.INSTAGRAM)
    dispatcher = await _dispatcher(recovery_container, recording_bot, command)
    message = settings_message(chat_id=GROUP_ID, actor_id=USER_ID).model_copy(update={"text": MEDIA_URL})
    _record_successful_refresh(recording_bot)

    await dispatcher.feed_update(recording_bot, Update(update_id=1, message=message))

    settings = await recovery_container.get(Settings)
    namespace = await recovery_container.get(RuntimeNamespace)
    published = broker_envelopes(settings, namespace.value)
    if restriction != "none":
        assert not published
        return
    assert len(published) == 1
    envelope = JobEnvelope.model_validate(published[0])
    assert envelope.feature == CommandName.INSTAGRAM
    assert envelope.target.telegram_id == GROUP_ID
    assert envelope.message_id == message.message_id
    ticket = await (await recovery_container.get(AdmissionPort)).get(envelope.job_id)
    assert ticket is not None
    assert ticket.state == TicketState.ENQUEUED
    assert (await (await recovery_container.get(ChatStatusService)).evaluate_access(GROUP_ID)).allowed


async def test_unavailable_media_verification_preserves_approval_and_recovers_after_cooldown(
    recovery_container: AsyncContainer, recording_bot: Bot
) -> None:
    """Deny uncertain requests without publication, bound immediate retries and recover the same approved group."""
    group_id = await _expire_administrators(recovery_container)
    command = await get_media_command(recovery_container, CommandName.INSTAGRAM)
    dispatcher = await _dispatcher(recovery_container, recording_bot, command)
    message = settings_message(chat_id=GROUP_ID, actor_id=USER_ID).model_copy(update={"text": MEDIA_URL})
    recording_bot.session.responses.extend(
        [
            member_payload("creator", is_bot=True),
            TelegramForbiddenError(GetChatAdministrators(chat_id=GROUP_ID), "Synthetic verification unavailable"),
        ]
    )
    for update_id in (1, 2):
        await dispatcher.feed_update(recording_bot, Update(update_id=update_id, message=message))
    statuses = await recovery_container.get(ChatStatusService)
    assert (await statuses.evaluate_access(GROUP_ID)).reason == DecisionReason.MEMBER_EVIDENCE_UNKNOWN
    assert await statuses.get_status(GROUP_ID) == ChatStatus.APPROVED
    assert sum(isinstance(call, GetChatAdministrators) for call in recording_bot.session.calls) == 1
    settings = await recovery_container.get(Settings)
    namespace = await recovery_container.get(RuntimeNamespace)
    assert not broker_envelopes(settings, namespace.value)

    await (await recovery_container.get(CachePort)).delete(f"membership:action_refresh:{group_id}:1")
    _record_successful_refresh(recording_bot)
    await dispatcher.feed_update(recording_bot, Update(update_id=3, message=message))

    assert (await statuses.evaluate_access(GROUP_ID)).allowed
    assert await statuses.get_status(GROUP_ID) == ChatStatus.APPROVED
    assert len(broker_envelopes(settings, namespace.value)) == 1
