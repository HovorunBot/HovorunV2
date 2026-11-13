"""Consolidated integration tests for Telegram settings menus, authorization, and persistence."""

from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText
from aiogram.types import InlineKeyboardMarkup, Message
from dishka import AsyncContainer

from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreferenceService,
)
from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.application.speech.ports import SpeechSettingsStore
from hovorunv2.domain.enums import SpeechMode
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.callbacks import (
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)
from tests.conftest import provision_role
from tests.utils.settings import bind_settings_callback, query_for, settings_message
from tests.utils.speech import GROUP, OTHER, USER, group_speech_target, private_speech_target
from tests.utils.telegram.session import RecordingBot


def _last_edit(bot: RecordingBot) -> EditMessageText:
    return next(c for c in reversed(bot.session.calls) if isinstance(c, EditMessageText))


def _unpack_callbacks(markup: InlineKeyboardMarkup | None) -> list[SettingsCallback]:
    assert markup is not None
    return [
        SettingsCallback.unpack(item.callback_data)
        for row in markup.inline_keyboard
        for item in row
        if item.callback_data is not None and item.callback_data.startswith("settings:")
    ]


async def _send_group_callback(
    container: AsyncContainer,
    command: SettingsCommand,
    bot: RecordingBot,
    message: Message,
    actor_id: int,
    callback: SettingsCallback,
) -> EditMessageText:
    bound = await bind_settings_callback(container, message, actor_id, callback)
    await command.handle_callback(query_for(message, bot, actor_id), bound)
    return _last_edit(bot)


async def test_group_settings_menus_authorization_and_persistence(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Group admin navigates speech, notifications, usage, and close; non-admin is rejected."""
    target = await group_speech_target(test_container)
    await provision_role(test_container, USER)
    command = await test_container.get(SettingsCommand)
    speech_store = await test_container.get(SpeechSettingsStore)
    sessions = await test_container.get(SettingsSessionService)
    message = settings_message(chat_id=GROUP, actor_id=USER)

    # 1. Open and update speech recognition mode as admin
    speech_edit = await _send_group_callback(
        test_container, command, recording_bot, message, USER, SettingsCallback(menu=SettingsMenu.SPEECH)
    )
    assert speech_edit.text is not None
    assert "Speech Recognition Settings" in speech_edit.text
    assert "Automatic recognition runs locally" in speech_edit.text

    recording_bot.session.responses.extend([True, True])
    await _send_group_callback(
        test_container,
        command,
        recording_bot,
        message,
        USER,
        SettingsCallback(
            menu=SettingsMenu.SPEECH,
            action=SettingsAction.SELECT,
            value=SpeechMode.MANUAL_AND_AUTOMATIC.value,
        ),
    )
    assert await speech_store.mode(target) == SpeechMode.MANUAL_AND_AUTOMATIC

    # 2. Non-admin cannot change group settings (speech or UI language)
    for cb in (
        SettingsCallback(menu=SettingsMenu.SPEECH, action=SettingsAction.SELECT, value=SpeechMode.OFF.value),
        SettingsCallback(menu=SettingsMenu.UI_LANG, action=SettingsAction.SELECT, value="uk"),
    ):
        recording_bot.session.responses.extend(
            [
                {"status": "member", "user": {"id": OTHER, "is_bot": False, "first_name": "NonAdmin"}},
                True,
            ]
        )
        await _send_group_callback(test_container, command, recording_bot, message, OTHER, cb)
    assert await speech_store.mode(target) == SpeechMode.MANUAL_AND_AUTOMATIC

    # 3. Group language menu includes interface language selector and usage view renders shared media quota
    lang_edit = await _send_group_callback(
        test_container, command, recording_bot, message, USER, SettingsCallback(menu=SettingsMenu.LANG)
    )
    assert lang_edit.text is not None
    assert "Language Settings" in lang_edit.text
    assert "Configure interface language, translation target" in lang_edit.text

    usage_edit = await _send_group_callback(
        test_container, command, recording_bot, message, USER, SettingsCallback(menu=SettingsMenu.USAGE, value="group")
    )
    assert usage_edit.text is not None
    assert "Group usage" in usage_edit.text
    assert usage_edit.text.count("Media uploads — shared across all providers") == 1
    assert "0/12 requests" in usage_edit.text
    assert "Youtube video links" not in usage_edit.text
    assert "Instagram video links" not in usage_edit.text
    assert "Safety caps" in usage_edit.text

    # 4. Group notification menu binds every category, then return to main and close
    notif_edit = await _send_group_callback(
        test_container,
        command,
        recording_bot,
        message,
        USER,
        SettingsCallback(menu=SettingsMenu.NOTIFICATIONS, value="group"),
    )
    assert notif_edit.text is not None
    assert "Notification preferences" in notif_edit.text
    notif_callbacks = _unpack_callbacks(notif_edit.reply_markup)
    assert {item.value for item in notif_callbacks if item.menu == SettingsMenu.NOTIFICATIONS} == {
        f"group.{category.value}" for category in NotificationCategory
    }
    for item in notif_callbacks:
        assert item.token
        operation = item.model_copy(update={"token": ""}).pack()
        assert await sessions.resolve(item.token, operation, USER, GROUP, message.message_id) is not None

    back = next(item for item in notif_callbacks if item.menu == SettingsMenu.MAIN)
    await command.handle_callback(query_for(message, recording_bot, USER), back)
    main_callbacks = _unpack_callbacks(_last_edit(recording_bot).reply_markup)
    close = next(item for item in main_callbacks if item.menu == SettingsMenu.CLOSE)
    recording_bot.session.responses.extend([True, True])
    await command.handle_callback(query_for(message, recording_bot, USER), close)

    assert isinstance(recording_bot.session.calls[-2], DeleteMessage)
    assert recording_bot.session.calls[-2].chat_id == GROUP
    assert recording_bot.session.calls[-2].message_id == message.message_id
    assert isinstance(recording_bot.session.calls[-1], AnswerCallbackQuery)
    close_op = close.model_copy(update={"token": ""}).pack()
    assert await sessions.resolve(close.token, close_op, USER, GROUP, message.message_id) is None


async def test_private_settings_menus_and_persistence(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Private user configures speech mode, toggles notification preference, and views own usage."""
    target = await private_speech_target(test_container)
    await provision_role(test_container, USER)
    command = await test_container.get(SettingsCommand)
    speech_store = await test_container.get(SpeechSettingsStore)
    notif_service = await test_container.get(NotificationPreferenceService)
    sessions = await test_container.get(SettingsSessionService)
    message = settings_message(chat_id=USER, actor_id=USER)
    context = await sessions.prepare_listing(USER, USER, message.message_id)

    async def _issue(cb: SettingsCallback) -> SettingsCallback:
        token = await sessions.issue(context, [cb.model_copy(update={"token": ""}).pack()])
        return cb.model_copy(update={"token": token})

    # 1. Speech mode selection in private chat
    speech_cb = await bind_settings_callback(
        test_container,
        message,
        USER,
        SettingsCallback(
            menu=SettingsMenu.SPEECH,
            action=SettingsAction.SELECT,
            value=SpeechMode.MANUAL_ONLY.value,
        ),
    )
    recording_bot.session.responses.extend([True, True])
    await command.handle_callback(query_for(message, recording_bot, USER), speech_cb)
    assert await speech_store.mode(target) == SpeechMode.MANUAL_ONLY

    # 2. Private notification preferences open and toggle
    notif_open = await _issue(SettingsCallback(menu=SettingsMenu.NOTIFICATIONS, value="own"))
    await command.handle_callback(query_for(message, recording_bot, USER), notif_open)
    notif_edit = _last_edit(recording_bot)
    assert notif_edit.text is not None
    assert "Notification preferences" in notif_edit.text
    release_toggle = next(cb for cb in _unpack_callbacks(notif_edit.reply_markup) if cb.value == "own.release")
    recording_bot.session.responses.extend([True, True])
    await command.handle_callback(query_for(message, recording_bot, USER), await _issue(release_toggle))

    toggled_edit = _last_edit(recording_bot)
    assert toggled_edit.reply_markup is not None
    labels = {
        SettingsCallback.unpack(item.callback_data).value: item.text
        for row in toggled_edit.reply_markup.inline_keyboard
        for item in row
        if item.callback_data is not None and item.callback_data.startswith("settings:")
    }
    assert "❌" in labels["own.release"]
    assert "✅" in labels["own.beta"]
    own = await notif_service.own_target(USER)
    assert await notif_service.resolve(own, NotificationCategory.RELEASE) is False
    assert await notif_service.resolve(own, NotificationCategory.BETA) is True

    # 3. Private UI language menu display and usage view distinguishing unlimited admin quota
    ui_lang_open = await _issue(SettingsCallback(menu=SettingsMenu.UI_LANG))
    await command.handle_callback(query_for(message, recording_bot, USER), ui_lang_open)
    ui_lang_edit = _last_edit(recording_bot)
    assert ui_lang_edit.text is not None
    assert "Personal Interface Language" in ui_lang_edit.text
    assert "Choose the interface language" in ui_lang_edit.text

    usage_open = await _issue(SettingsCallback(menu=SettingsMenu.USAGE, value="own"))
    await command.handle_callback(query_for(message, recording_bot, USER), usage_open)
    usage_edit = _last_edit(recording_bot)
    assert usage_edit.text is not None
    assert "Your usage" in usage_edit.text
    assert "personal quota unlimited" in usage_edit.text
    assert "Safety caps" in usage_edit.text
