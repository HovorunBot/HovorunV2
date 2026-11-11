"""Keep active settings usable and expire them after thirty seconds without valid actions."""

import time

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText
from dishka import AsyncContainer

from hovorunv2.application.settings import settings_session_service
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.callbacks import SettingsAction, SettingsCallback, SettingsMenu
from tests.conftest import provision_role
from tests.utils.runtime import ManualClock
from tests.utils.runtimes.beta import FIRST, group_beta_runtime
from tests.utils.settings import bind_settings_callback, query_for, settings_message
from tests.utils.telegram.session import RecordingSession

ACTIVITY_INTERVAL = 20
IDLE_INTERVAL = 31


def _buttons(transport: RecordingSession) -> list[SettingsCallback]:
    """Read callbacks from the most recent real settings keyboard."""
    edit = next(call for call in reversed(transport.calls) if isinstance(call, EditMessageText))
    assert edit.reply_markup is not None
    return [
        SettingsCallback.unpack(button.callback_data)
        for row in edit.reply_markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]


@pytest.mark.parametrize("page_indicator", [False, True])
async def test_settings_activity_renews_session_until_idle(
    test_container: AsyncContainer,
    recording_bot: Bot,
    monkeypatch: pytest.MonkeyPatch,
    *,
    page_indicator: bool,
) -> None:
    """Navigate beyond the opening deadline, including page indicators, then reject an idle callback."""
    transport = recording_bot.session
    assert isinstance(transport, RecordingSession)
    await group_beta_runtime(test_container)
    await provision_role(test_container, FIRST)
    command = await test_container.get(SettingsCommand)
    message = settings_message()
    clock = ManualClock(time.time())
    monkeypatch.setattr(settings_session_service.time, "time", clock.now)
    initial = await bind_settings_callback(test_container, message, FIRST, SettingsCallback(menu=SettingsMenu.MAIN))
    query = query_for(message, recording_bot)
    await command.handle_callback(query, initial)
    menus = [SettingsMenu.LANG, SettingsMenu.LANG_TARGET, SettingsMenu.LANG]
    if page_indicator:
        menus.insert(2, SettingsMenu.LANG_TARGET)
    for index, menu in enumerate(menus):
        callbacks = _buttons(transport)
        action = SettingsAction.PAGE if page_indicator and index == 2 else None
        callback = next(item for item in callbacks if item.menu == menu and item.action == action)
        clock.value += ACTIVITY_INTERVAL
        before = len([call for call in transport.calls if isinstance(call, EditMessageText)])
        transport.responses.append(True)
        await command.handle_callback(query, callback)
        edits = [call for call in transport.calls if isinstance(call, EditMessageText)]
        assert len(edits) == before + 1
        last_edit_text = edits[-1].text
        assert last_edit_text is not None
        assert "expired" not in last_edit_text.lower()
        assert not any(isinstance(call, DeleteMessage) for call in transport.calls)
    current = next(item for item in _buttons(transport) if item.menu == SettingsMenu.MAIN)
    clock.value += IDLE_INTERVAL
    before_idle = len(transport.calls)
    transport.responses.append(True)
    await command.handle_callback(query, current)
    idle_calls = transport.calls[before_idle:]
    assert not any(isinstance(call, EditMessageText | DeleteMessage) for call in idle_calls)
    assert any(isinstance(call, AnswerCallbackQuery) and call.show_alert for call in idle_calls)


async def test_rejected_settings_action_does_not_renew_inactivity(
    test_container: AsyncContainer, recording_bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forged operation cannot keep a window alive beyond its last accepted activity."""
    transport = recording_bot.session
    assert isinstance(transport, RecordingSession)
    await group_beta_runtime(test_container)
    await provision_role(test_container, FIRST)
    command = await test_container.get(SettingsCommand)
    message = settings_message()
    clock = ManualClock(time.time())
    monkeypatch.setattr(settings_session_service.time, "time", clock.now)
    callback = await bind_settings_callback(test_container, message, FIRST, SettingsCallback(menu=SettingsMenu.MAIN))
    forged = callback.model_copy(update={"menu": SettingsMenu.LANG})
    clock.value += ACTIVITY_INTERVAL
    transport.responses.append(True)
    await command.handle_callback(query_for(message, recording_bot), forged)
    clock.value += IDLE_INTERVAL - ACTIVITY_INTERVAL
    transport.responses.append(True)
    await command.handle_callback(query_for(message, recording_bot), callback)
    assert len(transport.calls) == 2
    assert all(isinstance(call, AnswerCallbackQuery) and call.show_alert for call in transport.calls)
