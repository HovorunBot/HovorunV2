"""Settings notification preference menus through the real Telegram transport."""

import json

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreferenceService,
)
from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.callbacks import SettingsCallback, SettingsMenu
from tests.conftest import provision_role, verify_group_admins
from tests.utils.activation import sent_fields
from tests.utils.settings import bind_settings_callback, button, query_for, settings_http, settings_message
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.telegram.session import RecordingSession

USER = 540001
GROUP = -540001


async def test_group_notification_menu_binds_every_category(test_container: AsyncContainer, recording_bot: Bot) -> None:
    """Render every group preference, then return and close using the current session."""
    await (await test_container.get(ChatStore)).get_or_create_chat(GROUP)
    await verify_group_admins(test_container, GROUP)
    await (await test_container.get(ChatStatusService)).approve(GROUP)
    await provision_role(test_container, USER)
    command = await test_container.get(SettingsCommand)
    message = settings_message(chat_id=GROUP, actor_id=USER)
    callback = await bind_settings_callback(
        test_container, message, USER, SettingsCallback(menu=SettingsMenu.NOTIFICATIONS, value="group")
    )

    await command.handle_callback(query_for(message, recording_bot, USER), callback)

    transport = recording_bot.session
    assert isinstance(transport, RecordingSession)
    edits = [call for call in transport.calls if isinstance(call, EditMessageText)]
    assert len(edits) == 1
    assert "Notification preferences" in edits[0].text
    markup = edits[0].reply_markup
    assert markup is not None
    callbacks = [
        SettingsCallback.unpack(item.callback_data)
        for row in markup.inline_keyboard
        for item in row
        if item.callback_data is not None
    ]
    assert {item.value for item in callbacks if item.menu == SettingsMenu.NOTIFICATIONS} == {
        f"group.{category.value}" for category in NotificationCategory
    }
    sessions = await test_container.get(SettingsSessionService)
    for item in callbacks:
        assert item.token
        operation = item.model_copy(update={"token": ""}).pack()
        assert await sessions.resolve(item.token, operation, USER, GROUP, message.message_id) is not None

    back = next(item for item in callbacks if item.menu == SettingsMenu.MAIN)
    await command.handle_callback(query_for(message, recording_bot, USER), back)
    main = next(call for call in reversed(transport.calls) if isinstance(call, EditMessageText))
    assert main.reply_markup is not None
    close = next(
        SettingsCallback.unpack(item.callback_data)
        for row in main.reply_markup.inline_keyboard
        for item in row
        if item.callback_data is not None and SettingsCallback.unpack(item.callback_data).menu == SettingsMenu.CLOSE
    )
    transport.responses.extend([True, True])
    await command.handle_callback(query_for(message, recording_bot, USER), close)

    assert isinstance(transport.calls[-2], DeleteMessage)
    assert transport.calls[-2].chat_id == GROUP
    assert transport.calls[-2].message_id == message.message_id
    assert isinstance(transport.calls[-1], AnswerCallbackQuery)
    operation = close.model_copy(update={"token": ""}).pack()
    assert await sessions.resolve(close.token, operation, USER, GROUP, message.message_id) is None


async def test_private_notification_toggle_renders_and_persists(test_container: AsyncContainer) -> None:
    """Own preferences open from private settings and one toggle persists."""
    await (await test_container.get(UserStore)).ingest_user(USER)
    command = await test_container.get(SettingsCommand)
    sessions = await test_container.get(SettingsSessionService)
    message = settings_message(chat_id=USER, actor_id=USER)
    context = await sessions.prepare_listing(USER, USER, message.message_id)
    open_callback = SettingsCallback(menu=SettingsMenu.NOTIFICATIONS, value="own")
    token = await sessions.issue(context, [open_callback.pack()])
    assert await sessions.resolve(token, open_callback.pack(), USER, message.chat.id, message.message_id) is not None
    async with Bot(BOT_CREDENTIAL) as bot:
        with aioresponses() as http:
            settings_http(http, message)
            await command.handle_callback(
                query_for(message, bot, USER), open_callback.model_copy(update={"token": token})
            )
            assert "Notification preferences" in sent_fields(http, "editMessageText")[-1]["text"]
            toggle = button(http, SettingsMenu.NOTIFICATIONS, value="own.release")
            toggle_token = await sessions.issue(context, [toggle.model_copy(update={"token": ""}).pack()])
            await command.handle_callback(
                query_for(message, bot, USER), toggle.model_copy(update={"token": toggle_token})
            )
            text = sent_fields(http, "editMessageText")[-1]["text"]
    assert "Notification preferences" in text
    markup = json.loads(sent_fields(http, "editMessageText")[-1]["reply_markup"])
    labels = {
        SettingsCallback.unpack(item["callback_data"]).value: item["text"]
        for row in markup["inline_keyboard"]
        for item in row
        if item["callback_data"].startswith("settings:")
    }
    assert "❌" in labels["own.release"]
    assert "✅" in labels["own.beta"]
    service = await test_container.get(NotificationPreferenceService)
    own = await service.own_target(USER)
    assert await service.resolve(own, NotificationCategory.RELEASE) is False
    assert await service.resolve(own, NotificationCategory.BETA) is True
