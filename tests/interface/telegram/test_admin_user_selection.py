"""Private administrator user selection with real Telegram models and persistence."""

from datetime import UTC, datetime

import pytest
from aiogram import Dispatcher
from aiogram.methods import EditMessageReplyMarkup, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    ReplyKeyboardMarkup,
    SharedUser,
    Update,
    User,
    UsersShared,
)
from dishka import AsyncContainer

from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.settings.admin_session_service import AdminSessionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Role
from hovorunv2.interface.telegram.handlers.administration import AdministrationCommand
from hovorunv2.interface.telegram.settings.callbacks import AdminCallback
from tests.conftest import provision_role
from tests.utils.telegram.session import RecordingBot

ADMIN_ID = 730001
TARGET_ID = 730002


def _message(bot: RecordingBot, actor_id: int = ADMIN_ID, **fields: object) -> Message:
    """Build a private Telegram message without involving profile or contact data."""
    return Message.model_validate(
        {
            "message_id": 1,
            "date": datetime.now(UTC),
            "chat": Chat(id=actor_id, type="private"),
            "from_user": User(id=actor_id, is_bot=False, first_name="Admin"),
            **fields,
        }
    ).as_(bot)


@pytest.mark.parametrize(
    "entry",
    ["/admin trust user", '/admin trust user @unknown_person --reason "Known personally"'],
)
async def test_user_picker_previews_unseen_user_and_requires_confirmation(
    test_container: AsyncContainer, recording_bot: RecordingBot, entry: str
) -> None:
    """Select an unseen user without knowing their ID, then explicitly confirm trust."""
    await provision_role(test_container, ADMIN_ID, Role.OWNER)
    command = await test_container.get(AdministrationCommand)
    dispatcher = Dispatcher()
    dispatcher.message.register(command.handle, command.trigger)
    command.register_callbacks(dispatcher, {})
    users = await test_container.get(UserStore)

    await dispatcher.feed_update(recording_bot, Update(update_id=1, message=_message(recording_bot, text=entry)))

    pickers = [
        call.reply_markup
        for call in recording_bot.session.calls
        if isinstance(call, SendMessage) and isinstance(call.reply_markup, ReplyKeyboardMarkup)
    ]
    assert len(pickers) == 1
    selection = pickers[0].keyboard[0][0].request_users
    assert selection is not None
    assert selection.user_is_bot is False
    assert selection.max_quantity == 1
    assert not selection.request_name
    assert not selection.request_username
    assert not selection.request_photo
    assert await users.get_by_telegram_id(TARGET_ID) is None
    shared = UsersShared(request_id=selection.request_id, users=[SharedUser(user_id=TARGET_ID)])

    await dispatcher.feed_update(
        recording_bot, Update(update_id=2, message=_message(recording_bot, users_shared=shared))
    )

    user = await users.get_by_telegram_id(TARGET_ID)
    assert user is not None
    assert not user.is_trusted
    if "--reason" in entry:
        previews = [call.text for call in recording_bot.session.calls if isinstance(call, SendMessage)]
        assert any("Known personally" in preview for preview in previews)
    edits = [call for call in recording_bot.session.calls if isinstance(call, EditMessageReplyMarkup)]
    assert len(edits) == 1
    keyboard = edits[0].reply_markup
    assert keyboard is not None
    callback_data = keyboard.inline_keyboard[0][0].callback_data
    assert callback_data is not None
    callback = CallbackQuery(
        id="trust-selected-user",
        from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
        chat_instance="private",
        message=_message(recording_bot, text="Confirm"),
        data=AdminCallback.unpack(callback_data).pack(),
    ).as_(recording_bot)

    await dispatcher.feed_update(recording_bot, Update(update_id=3, callback_query=callback))

    user = await users.get_by_telegram_id(TARGET_ID)
    assert user is not None
    assert user.is_trusted


@pytest.mark.parametrize("denial", ["other_actor", "demoted", "unissued", "multiple_users", "deleted_target"])
async def test_user_selection_rejects_invalid_or_unauthorized_submissions(
    test_container: AsyncContainer, recording_bot: RecordingBot, denial: str
) -> None:
    """Selections cannot cross actors, bypass authority, create tombstones or select multiple users."""
    await provision_role(test_container, ADMIN_ID, Role.OWNER)
    sessions = await test_container.get(AdminSessionService)
    users = await test_container.get(UserStore)
    command = await test_container.get(AdministrationCommand)
    request_id = await sessions.issue_user_selection(ADMIN_ID)
    actor_id = ADMIN_ID
    selected_ids = [TARGET_ID]
    if denial == "other_actor":
        actor_id = await provision_role(test_container, ADMIN_ID + 10, Role.ADMIN)
    if denial == "demoted":
        await (await test_container.get(RoleService)).set_role(ADMIN_ID, Role.USER, ActorContext.cli())
    if denial == "unissued":
        request_id = -1
    if denial == "multiple_users":
        selected_ids.append(TARGET_ID + 1)
    if denial == "deleted_target":
        await users.ingest_user(TARGET_ID)
        await users.soft_delete_user(TARGET_ID, ActorContext.cli(), "synthetic deletion")
    shared = UsersShared(request_id=request_id, users=[SharedUser(user_id=user_id) for user_id in selected_ids])

    await command.handle_users_shared(_message(recording_bot, actor_id, users_shared=shared))

    assert await users.get_by_telegram_id(TARGET_ID) is None
    assert not any(isinstance(call, EditMessageReplyMarkup) for call in recording_bot.session.calls)


async def test_user_selection_is_single_use(test_container: AsyncContainer, recording_bot: RecordingBot) -> None:
    """A second selection cannot reuse a picker to target another identity."""
    await provision_role(test_container, ADMIN_ID, Role.OWNER)
    sessions = await test_container.get(AdminSessionService)
    command = await test_container.get(AdministrationCommand)
    request_id = await sessions.issue_user_selection(ADMIN_ID)
    shared = UsersShared(request_id=request_id, users=[SharedUser(user_id=TARGET_ID)])

    await command.handle_users_shared(_message(recording_bot, users_shared=shared))
    replay = UsersShared(request_id=request_id, users=[SharedUser(user_id=TARGET_ID + 1)])
    await command.handle_users_shared(_message(recording_bot, users_shared=replay))

    users = await test_container.get(UserStore)
    assert await users.get_by_telegram_id(TARGET_ID + 1) is None
    assert len([call for call in recording_bot.session.calls if isinstance(call, EditMessageReplyMarkup)]) == 1
