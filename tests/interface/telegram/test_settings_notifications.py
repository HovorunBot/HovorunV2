"""Settings notification preference menus through the real Telegram transport."""

import json

from aiogram import Bot
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreferenceService,
)
from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.callbacks import SettingsCallback, SettingsMenu
from tests.utils.activation import sent_fields
from tests.utils.settings import button, query_for, settings_http, settings_message
from tests.utils.telegram import BOT_CREDENTIAL

USER = 540001


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
