"""Behavioral tests for localized administrative feedback, error alerts, and callback answers."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiogram_i18n import I18nMiddleware
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.settings.admin_session_service import AdminSessionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform, Role
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository
from hovorunv2.interface.access_arguments import parse_access
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.administration import AdministrationCommand
from hovorunv2.interface.telegram.handlers.beta import BetaPromptCommand
from hovorunv2.interface.telegram.handlers.emergency import EmergencyCommand
from hovorunv2.interface.telegram.handlers.recovery import RecoveryCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.i18n import AudienceI18nManager, create_i18n_core, get_message
from hovorunv2.interface.telegram.settings.callbacks import (
    AccessAction,
    AccessCallback,
    AdminCallback,
    BetaAction,
    BetaCallback,
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)
from tests.conftest import provision_role
from tests.utils.settings.sessions import bind_settings_callback
from tests.utils.telegram.session import RecordingBot


@pytest.mark.parametrize(
    ("cancel", "repeat", "expected"),
    [
        (False, False, "Зміни збережено."),
        (False, True, "Зміни збережено."),
        (True, False, "Дію скасовано."),
    ],
)
async def test_admin_confirmation_has_readable_feedback(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
    *,
    cancel: bool,
    repeat: bool,
    expected: str,
) -> None:
    """Confirm trust changes and cancellation through readable private Telegram replies."""
    actor_id = 720040
    target_id = 720041
    await provision_role(test_container, actor_id, Role.OWNER)
    await _set_user_locale(test_container, target_id, "en")
    administration = await test_container.get(AdministrationService)
    sessions = await test_container.get(AdminSessionService)
    request = parse_access(["trust", "user", str(target_id)])
    target = await administration.resolve(request.kind, request.target)
    actor = ActorContext.telegram(actor_id)
    if repeat:
        await administration.execute(request, target, actor, "private")
    token = await sessions.issue(request, target, actor_id, 80)
    command = await test_container.get(AdministrationCommand)
    query = CallbackQuery(
        id="admin-confirmation",
        from_user=User(id=actor_id, first_name="Admin", is_bot=False),
        chat_instance="synth",
        message=_make_message(
            recording_bot, chat_id=actor_id, chat_type="private", user_id=actor_id, text="Confirm", message_id=80
        ),
    ).as_(recording_bot)

    await command.handle_callback(query, AdminCallback(token=token, cancel=cancel))

    edits = [call for call in recording_bot.session.calls if isinstance(call, EditMessageText)]
    assert len(edits) == 1
    assert edits[0].text == expected
    assert edits[0].reply_markup is None
    state = await administration.inspect(target, actor, "private")
    assert state["trusted"] is not cancel


def _make_message(
    bot: RecordingBot,
    *,
    chat_id: int,
    chat_type: str,
    user_id: int,
    text: str,
    message_id: int = 1,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type, title="Admin Test Group" if chat_type != "private" else None),
        from_user=User(id=user_id, is_bot=False, first_name="Admin"),
        text=text,
    ).as_(bot)


async def _set_user_locale(container: AsyncContainer, user_id: int, locale: str) -> None:
    session_factory = await container.get(async_sessionmaker)
    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        user_db, _ = await user_repo.ingest(user_id)
        assert user_db is not None
        user_db.ui_locale = locale
        await session.commit()


@pytest.mark.parametrize("cancel", [False, True])
async def test_admin_can_preview_and_confirm_trust_for_unseen_numeric_user(
    test_container: AsyncContainer, recording_bot: RecordingBot, *, cancel: bool
) -> None:
    """Keep unseen users untrusted until their numeric-ID preview is confirmed."""
    actor_id, target_id = 720050, 720051
    await provision_role(test_container, actor_id, Role.OWNER)
    command = await test_container.get(AdministrationCommand)
    administration = await test_container.get(AdministrationService)
    recording_bot.session.responses.append({"message_id": 80, "date": 1, "chat": {"id": actor_id, "type": "private"}})
    message = _make_message(
        recording_bot,
        chat_id=actor_id,
        chat_type="private",
        user_id=actor_id,
        text=f"/admin trust user {target_id}",
    )

    await command.handle(message, recording_bot)

    keyboards = [call for call in recording_bot.session.calls if isinstance(call, EditMessageReplyMarkup)]
    assert len(keyboards) == 1
    keyboard = keyboards[0].reply_markup
    assert keyboard is not None
    callback_data = keyboard.inline_keyboard[0][int(cancel)].callback_data
    assert callback_data is not None
    target = await administration.resolve(parse_access(["trust", "user", str(target_id)]).kind, str(target_id))
    actor = ActorContext.telegram(actor_id)
    assert (await administration.inspect(target, actor, "private"))["trusted"] is False
    query = CallbackQuery(
        id="unseen-trust",
        from_user=User(id=actor_id, is_bot=False, first_name="Admin"),
        chat_instance="synth",
        message=_make_message(
            recording_bot, chat_id=actor_id, chat_type="private", user_id=actor_id, text="Confirm", message_id=80
        ),
    ).as_(recording_bot)

    await command.handle_callback(query, AdminCallback.unpack(callback_data))

    assert (await administration.inspect(target, actor, "private"))["trusted"] is not cancel


async def test_admin_unknown_handle_explains_numeric_id_alternative(
    test_container: AsyncContainer, recording_bot: RecordingBot
) -> None:
    """Distinguish unknown usernames from a private administrator's permission failure."""
    actor_id = 720052
    await provision_role(test_container, actor_id, Role.OWNER)
    command = await test_container.get(AdministrationCommand)
    message = _make_message(
        recording_bot, chat_id=actor_id, chat_type="private", user_id=actor_id, text="/admin trust user @unseen_user"
    )

    await command.handle(message, recording_bot)

    replies = [call.text for call in recording_bot.session.calls if isinstance(call, SendMessage)]
    assert len(replies) == 2
    assert "Telegram ID" in replies[0]
    assert not any(isinstance(call, EditMessageReplyMarkup) for call in recording_bot.session.calls)


async def test_admin_emergency_recovery_localized_in_uk_and_en(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify /admin, /emergency, and /recover render help and rejections in user's locale."""
    uk_admin_id = 720001
    en_non_admin_id = 720002
    await provision_role(test_container, uk_admin_id, Role.OWNER)
    await _set_user_locale(test_container, uk_admin_id, "uk")
    await _set_user_locale(test_container, en_non_admin_id, "en")

    session_factory = await test_container.get(async_sessionmaker)
    admin_cmd = await test_container.get(AdministrationCommand)
    emergency_cmd = await test_container.get(EmergencyCommand)
    recovery_cmd = await test_container.get(RecoveryCommand)

    dp = Dispatcher()
    dp.message.register(admin_cmd.handle, admin_cmd.trigger)
    dp.message.register(emergency_cmd.handle, emergency_cmd.trigger)
    dp.message.register(recovery_cmd.handle, recovery_cmd.trigger)
    I18nMiddleware(
        core=create_i18n_core(load_now=True),
        manager=AudienceI18nManager(session_maker=session_factory, default_locale="en"),
    ).setup(dp)

    for idx, text in enumerate(("/admin", "/emergency", "/recover"), start=1):
        await dp.feed_update(
            recording_bot,
            Update(
                update_id=idx,
                message=_make_message(
                    recording_bot,
                    chat_id=uk_admin_id,
                    chat_type="private",
                    user_id=uk_admin_id,
                    text=text,
                    message_id=idx,
                ),
            ),
        )
        await dp.feed_update(
            recording_bot,
            Update(
                update_id=idx + 10,
                message=_make_message(
                    recording_bot,
                    chat_id=en_non_admin_id,
                    chat_type="private",
                    user_id=en_non_admin_id,
                    text=text,
                    message_id=idx + 10,
                ),
            ),
        )

    uk_replies = [
        c.text for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == uk_admin_id
    ]
    en_replies = [
        c.text for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == en_non_admin_id
    ]

    assert len(uk_replies) == 3
    assert "Використання: /admin" in uk_replies[0]
    assert "Використання: /emergency" in uk_replies[1]
    assert "Використовуйте /recover" in uk_replies[2]

    assert len(en_replies) == 3
    assert "Administration unavailable." in en_replies[0]
    assert "Emergency controls unavailable." in en_replies[1]
    assert "Recovery inspection unavailable." in en_replies[2]


async def test_access_callback_alert_uses_clicking_user_locale(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify group access callback alert uses clicking user's locale even when group is English."""
    uk_user_id = 720010
    group_id = -100720011
    await _set_user_locale(test_container, uk_user_id, "uk")

    session_factory = await test_container.get(async_sessionmaker)
    async with session_factory() as session:
        chat = ChatDB(
            chat_id=group_id,
            platform=Platform.TELEGRAM,
            status=ChatStatus.UNAUTHORIZED,
            chat_type="supergroup",
            ui_locale="en",
        )
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        entity_id = chat.id
        generation = chat.generation

    access_cmd = await test_container.get(AccessCommand)
    group_msg = _make_message(
        recording_bot, chat_id=group_id, chat_type="supergroup", user_id=uk_user_id, text="/allow_chat", message_id=50
    )
    query = CallbackQuery(
        id="cb-access-uk",
        from_user=User(id=uk_user_id, first_name="Clicker", is_bot=False),
        chat_instance="synth",
        message=group_msg,
    ).as_(recording_bot)

    recording_bot.session.responses.append(True)
    await access_cmd.handle_callback(
        query,
        AccessCallback(chat_id=group_id, group_id=entity_id, generation=generation, action=AccessAction.REQUEST),
        recording_bot,
    )

    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert len(answers) == 1
    assert answers[0].text is not None
    assert "Довірені користувачі" in answers[0].text


async def test_beta_callback_alert_and_pluralization(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify beta callback alert uses clicking user's locale and beta-queued pluralizes in uk/en."""
    uk_user_id = 720020
    group_id = -100720021
    await _set_user_locale(test_container, uk_user_id, "uk")

    beta_cmd = await test_container.get(BetaPromptCommand)
    group_msg = _make_message(
        recording_bot, chat_id=group_id, chat_type="supergroup", user_id=uk_user_id, text="beta", message_id=60
    )
    query = CallbackQuery(
        id="cb-beta-uk",
        from_user=User(id=uk_user_id, first_name="Clicker", is_bot=False),
        chat_instance="synth",
        message=group_msg,
    ).as_(recording_bot)

    recording_bot.session.responses.append(True)
    await beta_cmd.handle_callback(query, BetaCallback(feature="media", action=BetaAction.FEATURES))
    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert len(answers) == 1
    assert answers[0].text == "Використовуйте /settings для вибору функцій."

    assert "1 отримувача" in get_message("beta-queued", "uk", count=1)
    assert "2 отримувачів" in get_message("beta-queued", "uk", count=2)
    assert "5 отримувачів" in get_message("beta-queued", "uk", count=5)
    assert "1 beta introduction recipient." in get_message("beta-queued", "en", count=1)
    assert "3 beta introduction recipients." in get_message("beta-queued", "en", count=3)


async def test_settings_callback_alert_uses_clicking_user_locale(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify settings callback toast uses clicking user's locale in an English group."""
    uk_admin_id = 720030
    group_id = -100720031
    await _set_user_locale(test_container, uk_admin_id, "uk")
    await provision_role(test_container, uk_admin_id)

    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(group_id)
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await (await test_container.get(GroupGrantStore)).approve(chat.id, ActorContext.cli())
    now = datetime.now(UTC) - timedelta(seconds=1)
    expiry = now + timedelta(minutes=5)
    evidence = MembershipEvidence(group_id, uk_admin_id, MembershipStatus.ADMIN, now, expiry)
    await (await test_container.get(MembershipStore)).save_admins(
        chat.id, AdministratorEvidence(group_id, True, (evidence,), now, expiry), ActorContext.cli()
    )

    command = await test_container.get(SettingsCommand)
    group_msg = _make_message(
        recording_bot, chat_id=group_id, chat_type="supergroup", user_id=uk_admin_id, text="/settings", message_id=70
    )
    query = CallbackQuery(
        id="cb-settings-uk",
        from_user=User(id=uk_admin_id, first_name="Admin", is_bot=False),
        chat_instance="synth",
        message=group_msg,
    ).as_(recording_bot)

    callback = await bind_settings_callback(
        test_container,
        group_msg,
        uk_admin_id,
        SettingsCallback(menu=SettingsMenu.LANG_TARGET, action=SettingsAction.SELECT, value="uk"),
    )
    recording_bot.session.responses.extend([True, True])
    await command.handle_callback(query, callback)

    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    edits = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)]
    assert len(answers) == 1
    assert answers[0].text == "Мову перекладу змінено на uk"
    assert len(edits) == 1
