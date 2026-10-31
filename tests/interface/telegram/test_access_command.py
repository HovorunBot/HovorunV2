"""Activation handlers and callbacks with real services, SQLite and intercepted Telegram HTTP."""

import json
from datetime import UTC, datetime

import pytest
from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.activation import ActivationOutcome, ActivationRequest, ActivationStore
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.domain.audit import AuditEventDB
from hovorunv2.domain.enums import CommandName, Role
from hovorunv2.domain.group_grant import GroupAccessRequestDB, GroupApprovalDB, RequestStatus
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.bot import build_router
from hovorunv2.interface.telegram.settings.callbacks import AccessAction, AccessCallback
from hovorunv2.interface.telegram.settings.prompts import TelegramUnauthorizedPromptSender
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware
from tests.conftest import provision_role
from tests.utils.activation import (
    ACTOR,
    ADMIN,
    BOT_ID,
    GROUP,
    OWNER,
    activation_runtime,
    group_message,
    seed_activation,
    sent_fields,
    stub_telegram,
)
from tests.utils.recorded_responses import RecordedResponses


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/allow_chat", True),
        (" /allow_chat ", True),
        ("/allow_chat extra", True),
        ("/other_command", False),
        ("", False),
        (None, False),
    ],
)
async def test_is_triggered(test_container: AsyncContainer, text: str | None, expected: bool) -> None:
    """Keep the existing command syntax using real aiogram messages."""
    message = group_message().model_copy(update={"text": text})
    async with activation_runtime(test_container) as runtime:
        assert await runtime.command.is_triggered(message) == expected


async def test_handle_authorized(test_container: AsyncContainer) -> None:
    """A bot administrator intentionally activates the known group and initializes defaults."""
    await seed_activation(test_container, trusted=False)
    await provision_role(test_container, ACTOR)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            await runtime.command.handle(group_message().as_(runtime.bot), runtime.bot)
            assert len(sent_fields(http, "sendMessage")) == 1
    commands = await test_container.get(CommandService)
    assert await commands.is_command_allowed(GROUP, CommandName.TIKTOK)


async def test_request_callback_uses_human_actor_and_deduplicates_admin_notifications(
    test_container: AsyncContainer,
) -> None:
    """A bot-authored menu persists the clicking human and notifies current ADMIN/OWNER once."""
    target = await seed_activation(test_container, trusted=False)
    await provision_role(test_container, ADMIN)
    await provision_role(test_container, OWNER, Role.OWNER)
    data = AccessCallback(
        chat_id=GROUP, group_id=target.entity_id, generation=target.generation, action=AccessAction.REQUEST
    )
    query = CallbackQuery(
        id="synthetic-request",
        from_user=User(id=ACTOR, is_bot=False, first_name="Synthetic human"),
        message=group_message(user_id=BOT_ID),
        chat_instance="synthetic",
        data=data.pack(),
    )
    async with activation_runtime(test_container) as runtime:
        query = query.as_(runtime.bot)
        with RecordedResponses() as http:
            stub_telegram(http)
            await runtime.command.handle_callback(query, data, runtime.bot)
            await runtime.command.handle_callback(query, data, runtime.bot)
            notifications = sent_fields(http, "sendMessage")
            assert {int(item["chat_id"]) for item in notifications} == {ADMIN, OWNER}
            assert len(notifications) == 2
            assert all(f"<code>{ACTOR}</code>" in item["text"] for item in notifications)
            assert all(f"<code>{BOT_ID}</code>" not in item["text"] for item in notifications)
            markup = json.loads(notifications[0]["reply_markup"])
            decision = AccessCallback.unpack(markup["inline_keyboard"][0][0]["callback_data"])
            assert decision.group_id == target.entity_id
            assert decision.generation == target.generation
            assert decision.request_id > 0
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        request = (await session.scalars(select(GroupAccessRequestDB))).one()
        event = (
            await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "group_activation"))
        ).one()
        assert request.requested_by_user_id == event.changed_fields["actor_user_id"]
        assert event.actor_id == ACTOR


async def test_trusted_button_click_activates_without_group_admin_call(test_container: AsyncContainer) -> None:
    """A trusted human can activate through a group menu authored by the bot."""
    target = await seed_activation(test_container)
    data = AccessCallback(chat_id=GROUP, group_id=target.entity_id, generation=1, action=AccessAction.REQUEST)
    query = CallbackQuery(
        id="trusted",
        from_user=User(id=ACTOR, is_bot=False, first_name="Synthetic"),
        chat_instance="test",
        message=group_message(user_id=BOT_ID),
        data=data.pack(),
    )
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            await runtime.command.handle_callback(query.as_(runtime.bot), data, runtime.bot)
            assert not sent_fields(http, "getChatMember")
            assert not sent_fields(http, "sendMessage")
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert len(list((await session.scalars(select(GroupApprovalDB))).all())) == 1


@pytest.mark.parametrize("invalid", ["non_admin", "group_transport", "wrong_group", "stale_generation", "legacy_menu"])
async def test_forged_or_stale_approval_callback_rejected(test_container: AsyncContainer, invalid: str) -> None:
    """Private decisions recheck current authority, target generation and pending request binding."""
    target = await seed_activation(test_container, trusted=False)
    await provision_role(test_container, ADMIN)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            pending = await runtime.service.enable(ACTOR, target, "group")
            actor = ACTOR if invalid == "non_admin" else ADMIN
            data = AccessCallback(
                chat_id=GROUP,
                group_id=target.entity_id,
                generation=target.generation,
                request_id=pending.request_id,
                action=AccessAction.APPROVE,
            )
            if invalid == "stale_generation":
                data.generation += 1
            elif invalid == "wrong_group":
                data.chat_id -= 1
            elif invalid == "legacy_menu":
                data.group_id = 0
            private = Message(
                message_id=3,
                date=datetime.now(UTC),
                chat=Chat(id=actor, type="private"),
                from_user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
                text="Review request",
            )
            query = CallbackQuery(
                id="forged",
                from_user=User(id=actor, is_bot=False, first_name="Synthetic"),
                chat_instance="test",
                message=group_message(user_id=BOT_ID) if invalid == "group_transport" else private,
                data=data.pack(),
            )
            await runtime.command.handle_callback(query.as_(runtime.bot), data, runtime.bot)
            assert not sent_fields(http, "sendMessage")
            assert not sent_fields(http, "editMessageText")
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert (await session.get(GroupAccessRequestDB, pending.request_id)).status == RequestStatus.PENDING
        assert not list((await session.scalars(select(GroupApprovalDB))).all())


async def test_private_admin_callback_approves_and_closes_exact_request(test_container: AsyncContainer) -> None:
    """Current bot ADMIN can approve from their private notification; replays send no group alert."""
    target = await seed_activation(test_container, trusted=False)
    await provision_role(test_container, ADMIN)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            pending = await runtime.service.enable(ACTOR, target, "group")
            data = AccessCallback(
                chat_id=GROUP,
                group_id=target.entity_id,
                generation=1,
                request_id=pending.request_id,
                action=AccessAction.APPROVE,
            )
            private = Message(
                message_id=2,
                date=datetime.now(UTC),
                chat=Chat(id=ADMIN, type="private"),
                from_user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
                text="Review request",
            )
            query = CallbackQuery(
                id="approve",
                from_user=User(id=ADMIN, is_bot=False, first_name="Admin"),
                chat_instance="test",
                message=private,
                data=data.pack(),
            ).as_(runtime.bot)
            await runtime.command.handle_callback(query, data, runtime.bot)
            await runtime.command.handle_callback(query, data, runtime.bot)
            assert len(sent_fields(http, "sendMessage")) == 1
            assert int(sent_fields(http, "sendMessage")[0]["chat_id"]) == GROUP


async def test_request_button_cannot_select_another_group(test_container: AsyncContainer) -> None:
    """The physical group carrying a request must match its callback target."""
    target = await seed_activation(test_container)
    data = AccessCallback(chat_id=GROUP - 1, group_id=target.entity_id, generation=1, action=AccessAction.REQUEST)
    query = CallbackQuery(
        id="wrong-target",
        from_user=User(id=ACTOR, is_bot=False, first_name="Human"),
        chat_instance="test",
        message=group_message(user_id=BOT_ID),
        data=data.pack(),
    )
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            await runtime.command.handle_callback(query.as_(runtime.bot), data, runtime.bot)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert not list((await session.scalars(select(GroupApprovalDB))).all())


async def test_prompt_contains_known_generation_bound_target(test_container: AsyncContainer) -> None:
    """Prompt delivery binds known groups and never provisions an unknown callback target."""
    target = await seed_activation(test_container)
    sender = await test_container.get(TelegramUnauthorizedPromptSender)
    async with activation_runtime(test_container) as runtime:
        with RecordedResponses() as http:
            stub_telegram(http)
            assert await sender.send(runtime.bot, GROUP)
            assert not await sender.send(runtime.bot, GROUP - 1)
            markup = json.loads(sent_fields(http, "sendMessage")[0]["reply_markup"])
            callback = AccessCallback.unpack(markup["inline_keyboard"][0][0]["callback_data"])
            assert callback.group_id == target.entity_id
            assert callback.generation == target.generation


async def test_real_router_activates_trusted_user_in_unapproved_group(test_container: AsyncContainer) -> None:
    """Production identity/access middleware and callbacks allow intentional trusted activation."""
    target = await seed_activation(test_container)
    async with activation_runtime(test_container) as runtime:
        router = build_router(
            [runtime.command],
            await test_container.get(MessageService),
            await test_container.get(AccessService),
            await test_container.get(Settings),
            await test_container.get(IdentityMiddleware),
            await test_container.get(CachePort),
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(router)
        data = AccessCallback(chat_id=GROUP, group_id=target.entity_id, generation=1, action=AccessAction.REQUEST)
        query = CallbackQuery(
            id="router",
            from_user=User(id=ACTOR, is_bot=False, first_name="Human"),
            chat_instance="test",
            message=group_message(user_id=BOT_ID),
            data=data.pack(),
        )
        with RecordedResponses() as http:
            stub_telegram(http)
            await dispatcher.feed_update(runtime.bot, Update(update_id=23, callback_query=query))
            assert len(sent_fields(http, "editMessageText")) == 1
            assert not sent_fields(http, "getChatMember")
    store = await test_container.get(ActivationStore)
    assert (
        await store.execute(ActivationRequest(ACTOR, target, "group"))
    ).outcome == ActivationOutcome.ALREADY_APPROVED
