"""Tests for three-state speech recognition settings in Telegram UI."""

from datetime import UTC, datetime

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, User
from aiohttp import FormData
from dishka import AsyncContainer

from hovorunv2.application.speech.ports import SpeechSettingsStore
from hovorunv2.domain.enums import SpeechMode
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.callbacks import (
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)
from tests.conftest import provision_role
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.settings import bind_settings_callback
from tests.utils.speech import GROUP, OTHER, USER, group_speech_target, private_speech_target
from tests.utils.telegram import RecordedResponse


async def test_speech_settings_menu_display(test_container: AsyncContainer) -> None:
    """Settings menu displays current mode and three choices with automatic disclosure."""
    await group_speech_target(test_container)
    await provision_role(test_container, USER)
    command = await test_container.get(SettingsCommand)
    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=789,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP, type="supergroup"),
    )
    query = CallbackQuery(
        id="synthetic",
        from_user=User(id=USER, first_name="Admin", is_bot=False),
        chat_instance="synthetic",
        message=message.as_(bot),
    ).as_(bot)
    callback = await bind_settings_callback(
        test_container,
        message,
        USER,
        SettingsCallback(menu=SettingsMenu.SPEECH),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/editMessageText",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(query, callback)
            edits = [
                call
                for (method, url), calls in responses.requests.items()
                if "editMessageText" in str(url)
                for call in calls
            ]
            assert len(edits) == 1
            request = edits[0]
            body = request.kwargs.get("data") or request.kwargs.get("json") or {}
            if isinstance(body, FormData):
                fields = {headers["name"]: value for headers, _options, value in body._fields}
                text_content = str(fields.get("text", ""))
            else:
                text_content = str(body)
            assert "Speech Recognition Settings" in text_content
            assert "Automatic recognition runs locally" in text_content
    finally:
        await bot.session.close()


async def test_speech_settings_mode_selection(test_container: AsyncContainer) -> None:
    """Authorized admin can change speech mode and change persists in database."""
    target = await group_speech_target(test_container)
    await provision_role(test_container, USER)
    command = await test_container.get(SettingsCommand)
    settings_store = await test_container.get(SpeechSettingsStore)
    assert await settings_store.mode(target) == SpeechMode.OFF

    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=789,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP, type="supergroup"),
    )
    query = CallbackQuery(
        id="synthetic",
        from_user=User(id=USER, first_name="Admin", is_bot=False),
        chat_instance="synthetic",
        message=message.as_(bot),
    ).as_(bot)
    callback = await bind_settings_callback(
        test_container,
        message,
        USER,
        SettingsCallback(
            menu=SettingsMenu.SPEECH,
            action=SettingsAction.SELECT,
            value=SpeechMode.MANUAL_AND_AUTOMATIC.value,
        ),
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
            assert await settings_store.mode(target) == SpeechMode.MANUAL_AND_AUTOMATIC
    finally:
        await bot.session.close()


async def test_speech_settings_unauthorized_rejection(test_container: AsyncContainer) -> None:
    """Non-admin user in group cannot access or change speech settings."""
    target = await group_speech_target(test_container)
    command = await test_container.get(SettingsCommand)
    settings_store = await test_container.get(SpeechSettingsStore)

    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=789,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP, type="supergroup"),
    )
    query = CallbackQuery(
        id="synthetic",
        from_user=User(id=OTHER, first_name="NonAdmin", is_bot=False),
        chat_instance="synthetic",
        message=message.as_(bot),
    ).as_(bot)
    callback = await bind_settings_callback(
        test_container,
        message,
        OTHER,
        SettingsCallback(
            menu=SettingsMenu.SPEECH,
            action=SettingsAction.SELECT,
            value=SpeechMode.MANUAL_AND_AUTOMATIC.value,
        ),
    )
    try:
        with RecordedResponses() as responses:
            responses.post(
                "https://api.telegram.org/bot777:synthetic-token/answerCallbackQuery",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(query, callback)
            # Stored mode remains OFF
            assert await settings_store.mode(target) == SpeechMode.OFF
    finally:
        await bot.session.close()


async def test_private_chat_speech_settings(test_container: AsyncContainer) -> None:
    """User in private chat can configure their own speech mode."""
    target = await private_speech_target(test_container)
    await provision_role(test_container, USER)
    command = await test_container.get(SettingsCommand)
    settings_store = await test_container.get(SpeechSettingsStore)
    assert await settings_store.mode(target) == SpeechMode.OFF

    bot = Bot("777:synthetic-token")
    message = Message(
        message_id=123,
        date=datetime.now(UTC),
        chat=Chat(id=USER, type="private"),
    )
    query = CallbackQuery(
        id="synthetic",
        from_user=User(id=USER, first_name="User", is_bot=False),
        chat_instance="synthetic",
        message=message.as_(bot),
    ).as_(bot)
    callback = await bind_settings_callback(
        test_container,
        message,
        USER,
        SettingsCallback(
            menu=SettingsMenu.SPEECH,
            action=SettingsAction.SELECT,
            value=SpeechMode.MANUAL_ONLY.value,
        ),
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
            assert await settings_store.mode(target) == SpeechMode.MANUAL_ONLY
    finally:
        await bot.session.close()
