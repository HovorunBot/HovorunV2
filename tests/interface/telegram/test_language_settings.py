"""Tests for interactive personal and group UI language settings."""

from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, User
from aiohttp import FormData
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.i18n import AudienceI18nManager
from hovorunv2.interface.telegram.settings.callbacks import (
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)
from tests.conftest import provision_role
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.settings.sessions import bind_settings_callback
from tests.utils.telegram import RecordedResponse

USER_ID = 640001
OTHER_USER_ID = 640002
GROUP_ID = -100640003


async def _setup_private_user(container: AsyncContainer, user_id: int = USER_ID) -> None:
    users = await container.get(UserStore)
    await users.ingest_user(user_id)
    await users.set_trust(user_id, is_trusted=True, actor=ActorContext.cli())
    chats = await container.get(ChatStore)
    await chats.get_or_create_chat(user_id)


async def _setup_group(container: AsyncContainer, group_id: int = GROUP_ID, admin_id: int = USER_ID) -> None:
    users = await container.get(UserStore)
    await users.ingest_user(admin_id)
    await users.ingest_user(OTHER_USER_ID)
    chats = await container.get(ChatStore)
    chat = await chats.get_or_create_chat(group_id)
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await (await container.get(GroupGrantStore)).approve(chat.id, ActorContext.cli())
    now = datetime.now(UTC) - timedelta(seconds=1)
    expiry = now + timedelta(minutes=5)
    evidence = MembershipEvidence(group_id, admin_id, MembershipStatus.ADMIN, now, expiry)
    await (await container.get(MembershipStore)).save_admins(
        chat.id, AdministratorEvidence(group_id, True, (evidence,), now, expiry), ActorContext.cli()
    )


async def _bind_private_listing_callback(
    container: AsyncContainer,
    message: Message,
    actor_id: int,
    callback: SettingsCallback,
) -> SettingsCallback:
    sessions = await container.get(SettingsSessionService)
    context = await sessions.prepare_listing(actor_id, message.chat.id, message.message_id)
    token = await sessions.issue(context, [callback.pack()])
    return callback.model_copy(update={"token": token})


def _extract_edit_text(responses: aioresponses) -> str:
    edits = [
        call for (method, url), calls in responses.requests.items() if "editMessageText" in str(url) for call in calls
    ]
    if not edits:
        return ""
    request = edits[0]
    body = request.kwargs.get("data") or request.kwargs.get("json") or {}
    if isinstance(body, FormData):
        fields = {headers["name"]: value for headers, _options, value in body._fields}
        return str(fields.get("text", ""))
    return str(body)


def _extract_callback_answer(responses: aioresponses) -> str:
    answers = [
        call
        for (method, url), calls in responses.requests.items()
        if "answerCallbackQuery" in str(url)
        for call in calls
    ]
    if not answers:
        return ""
    request = answers[0]
    body = request.kwargs.get("data") or request.kwargs.get("json") or {}
    if isinstance(body, FormData):
        fields = {headers["name"]: value for headers, _options, value in body._fields}
        return str(fields.get("text", ""))
    if isinstance(body, dict):
        return str(body.get("text", ""))
    return str(body)


async def test_private_ui_language_menu_display(test_container: AsyncContainer) -> None:
    """Private settings displays personal interface language options."""
    await _setup_private_user(test_container)
    command = await test_container.get(SettingsCommand)
    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=101,
        date=datetime.now(UTC),
        chat=Chat(id=USER_ID, type="private"),
    )
    query = CallbackQuery(
        id="q-ui-lang-disp",
        from_user=User(id=USER_ID, first_name="Alice", is_bot=False),
        chat_instance="synth",
        message=message.as_(bot),
    ).as_(bot)
    callback = await _bind_private_listing_callback(
        test_container,
        message,
        USER_ID,
        SettingsCallback(menu=SettingsMenu.UI_LANG),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/editMessageText",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(query, callback)
            text = _extract_edit_text(responses)
            assert "Personal Interface Language" in text
            assert "Choose the interface language" in text
    finally:
        await bot.session.close()


async def test_private_ui_language_select_ukrainian(test_container: AsyncContainer) -> None:
    """User selects Ukrainian in private chat; preference persists in database."""
    await _setup_private_user(test_container)
    command = await test_container.get(SettingsCommand)
    i18n_manager = await test_container.get(AudienceI18nManager)
    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=102,
        date=datetime.now(UTC),
        chat=Chat(id=USER_ID, type="private"),
    )
    query = CallbackQuery(
        id="q-ui-lang-uk",
        from_user=User(id=USER_ID, first_name="Alice", is_bot=False),
        chat_instance="synth",
        message=message.as_(bot),
    ).as_(bot)
    callback = await _bind_private_listing_callback(
        test_container,
        message,
        USER_ID,
        SettingsCallback(menu=SettingsMenu.UI_LANG, action=SettingsAction.SELECT, value="uk"),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/answerCallbackQuery",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/editMessageText",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(query, callback)
            answer = _extract_callback_answer(responses)
            assert "Мову успішно змінено на українську" in answer
            persisted_locale = await i18n_manager.get_user_locale(USER_ID)
            assert persisted_locale == "uk"
    finally:
        await bot.session.close()


async def test_private_ui_language_select_english(test_container: AsyncContainer) -> None:
    """User switches interface language to English; preference persists."""
    await _setup_private_user(test_container)
    command = await test_container.get(SettingsCommand)
    i18n_manager = await test_container.get(AudienceI18nManager)
    await i18n_manager.set_user_locale(USER_ID, "uk")
    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=103,
        date=datetime.now(UTC),
        chat=Chat(id=USER_ID, type="private"),
    )
    query = CallbackQuery(
        id="q-ui-lang-en",
        from_user=User(id=USER_ID, first_name="Alice", is_bot=False),
        chat_instance="synth",
        message=message.as_(bot),
    ).as_(bot)
    callback = await _bind_private_listing_callback(
        test_container,
        message,
        USER_ID,
        SettingsCallback(menu=SettingsMenu.UI_LANG, action=SettingsAction.SELECT, value="en"),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/answerCallbackQuery",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/editMessageText",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(query, callback)
            answer = _extract_callback_answer(responses)
            assert "Language successfully changed to English" in answer
            persisted_locale = await i18n_manager.get_user_locale(USER_ID)
            assert persisted_locale == "en"
    finally:
        await bot.session.close()


async def test_group_ui_language_select_by_admin(test_container: AsyncContainer) -> None:
    """Group administrator sets group UI language; persists on chat entity."""
    await _setup_group(test_container)
    await provision_role(test_container, USER_ID)
    command = await test_container.get(SettingsCommand)
    i18n_manager = await test_container.get(AudienceI18nManager)
    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=201,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP_ID, type="supergroup"),
    )
    query = CallbackQuery(
        id="q-grp-lang-uk",
        from_user=User(id=USER_ID, first_name="Admin", is_bot=False),
        chat_instance="synth",
        message=message.as_(bot),
    ).as_(bot)
    callback = await bind_settings_callback(
        test_container,
        message,
        USER_ID,
        SettingsCallback(menu=SettingsMenu.UI_LANG, action=SettingsAction.SELECT, value="uk"),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/answerCallbackQuery",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/editMessageText",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(query, callback)
            answer = _extract_callback_answer(responses)
            assert "Мову групи успішно змінено на українську" in answer
            chat_locale = await i18n_manager.get_chat_locale(GROUP_ID)
            assert chat_locale == "uk"
    finally:
        await bot.session.close()


async def test_group_ui_language_non_admin_rejected(test_container: AsyncContainer) -> None:
    """Non-admin user cannot access or change group UI language."""
    await _setup_group(test_container)
    command = await test_container.get(SettingsCommand)
    i18n_manager = await test_container.get(AudienceI18nManager)
    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=202,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP_ID, type="supergroup"),
    )
    query = CallbackQuery(
        id="q-grp-lang-nonadmin",
        from_user=User(id=OTHER_USER_ID, first_name="Bob", is_bot=False),
        chat_instance="synth",
        message=message.as_(bot),
    ).as_(bot)
    callback = await bind_settings_callback(
        test_container,
        message,
        OTHER_USER_ID,
        SettingsCallback(menu=SettingsMenu.UI_LANG, action=SettingsAction.SELECT, value="uk"),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/answerCallbackQuery",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(query, callback)
            answer = _extract_callback_answer(responses)
            assert "Settings unavailable" in answer
            chat_locale = await i18n_manager.get_chat_locale(GROUP_ID)
            assert chat_locale is None
    finally:
        await bot.session.close()


async def test_group_lang_menu_includes_interface_button(test_container: AsyncContainer) -> None:
    """Languages menu in group displays interface language selector button."""
    await _setup_group(test_container)
    await provision_role(test_container, USER_ID)
    command = await test_container.get(SettingsCommand)
    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=203,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP_ID, type="supergroup"),
    )
    query = CallbackQuery(
        id="q-grp-lang-menu",
        from_user=User(id=USER_ID, first_name="Admin", is_bot=False),
        chat_instance="synth",
        message=message.as_(bot),
    ).as_(bot)
    callback = await bind_settings_callback(
        test_container,
        message,
        USER_ID,
        SettingsCallback(menu=SettingsMenu.LANG),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/editMessageText",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(query, callback)
            text = _extract_edit_text(responses)
            assert "Language Settings" in text
            assert "Configure interface language, translation target" in text
    finally:
        await bot.session.close()
