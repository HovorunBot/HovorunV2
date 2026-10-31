"""Settings usage views for own and group scopes through the real Telegram transport."""

from aiogram import Bot
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.callbacks import SettingsCallback, SettingsMenu
from tests.conftest import provision_role, verify_group_admins
from tests.utils.activation import sent_fields
from tests.utils.settings import bind_settings_callback, query_for, settings_http, settings_message
from tests.utils.telegram import BOT_CREDENTIAL

GROUP = -480001
ACTOR = 480002


async def _approved_group(container: AsyncContainer) -> None:
    """Provision a known, approved group and an administrative actor."""
    await (await container.get(ChatStore)).get_or_create_chat(GROUP)
    await verify_group_admins(container, GROUP)
    await (await container.get(ChatStatusService)).approve(GROUP)
    await provision_role(container, ACTOR)


async def test_group_usage_view_renders_current_group_scope(test_container: AsyncContainer) -> None:
    """The group settings tab resolves the current group scope and safety caps."""
    await _approved_group(test_container)
    command = await test_container.get(SettingsCommand)
    message = settings_message(chat_id=GROUP, actor_id=ACTOR)
    callback = await bind_settings_callback(
        test_container, message, ACTOR, SettingsCallback(menu=SettingsMenu.USAGE, value="group")
    )
    async with Bot(BOT_CREDENTIAL) as bot:
        with aioresponses() as http:
            settings_http(http, message)
            await command.handle_callback(query_for(message, bot, ACTOR), callback)
            text = sent_fields(http, "editMessageText")[-1]["text"]
    assert "Group usage" in text
    assert "Safety caps" in text
    assert "requests" in text


async def test_private_usage_view_distinguishes_unlimited_admin_quota(test_container: AsyncContainer) -> None:
    """Private own-usage scope marks administrator quota unlimited while listing safety caps."""
    await _approved_group(test_container)
    command = await test_container.get(SettingsCommand)
    sessions = await test_container.get(SettingsSessionService)
    message = settings_message(chat_id=ACTOR, actor_id=ACTOR)
    context = await sessions.prepare_listing(ACTOR, ACTOR, message.message_id)
    callback = SettingsCallback(menu=SettingsMenu.USAGE, value="own")
    token = await sessions.issue(context, [callback.pack()])
    bound = callback.model_copy(update={"token": token})
    async with Bot(BOT_CREDENTIAL) as bot:
        with aioresponses() as http:
            settings_http(http, message)
            await command.handle_callback(query_for(message, bot, ACTOR), bound)
            text = sent_fields(http, "editMessageText")[-1]["text"]
    assert "Your usage" in text
    assert "personal quota unlimited" in text
    assert "Safety caps" in text
