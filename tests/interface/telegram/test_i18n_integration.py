"""End-to-end integration tests for bilingual Telegram user interactions."""

from datetime import UTC, datetime, timedelta

from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiogram_i18n import I18nMiddleware
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
from hovorunv2.interface.telegram.handlers.feedback import FeedbackCommand
from hovorunv2.interface.telegram.handlers.help import HelpCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.i18n import AudienceI18nManager
from hovorunv2.interface.telegram.settings.callbacks import (
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)
from tests.conftest import provision_role
from tests.utils.settings.sessions import bind_settings_callback
from tests.utils.telegram.session import RecordingBot


async def _build_dispatcher(container: AsyncContainer) -> Dispatcher:
    dp = Dispatcher()
    help_cmd = await container.get(HelpCommand)
    feedback_cmd = await container.get(FeedbackCommand)
    settings_cmd = await container.get(SettingsCommand)
    i18n_middleware = await container.get(I18nMiddleware)

    dp.message.register(help_cmd.handle, help_cmd.trigger)
    dp.message.register(feedback_cmd.handle, feedback_cmd.trigger)
    dp.message.register(settings_cmd.handle, settings_cmd.trigger)
    dp.callback_query.register(settings_cmd.handle_callback, SettingsCallback.filter())
    i18n_middleware.setup(dp)
    return dp


def _make_message(
    bot: RecordingBot,
    *,
    chat_id: int,
    chat_type: str,
    user_id: int,
    text: str,
    message_id: int,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type, title="Integration Group" if chat_type != "private" else None),
        from_user=User(id=user_id, is_bot=False, first_name="Tester"),
        text=text,
    ).as_(bot)


async def test_e2e_private_language_switch_immediate_effect(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """User switches private UI language to Ukrainian and immediately sees Ukrainian /start and /help."""
    user_id = 730001
    users = await test_container.get(UserStore)
    await users.ingest_user(user_id)
    await users.set_trust(user_id, is_trusted=True, actor=ActorContext.cli())
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(user_id)

    dp = await _build_dispatcher(test_container)

    # 1. Default unconfigured user receives English /start
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=1,
            message=_make_message(
                recording_bot, chat_id=user_id, chat_type="private", user_id=user_id, text="/start", message_id=1
            ),
        ),
    )
    first_msg = next(c for c in reversed(recording_bot.session.calls) if isinstance(c, SendMessage))
    assert "Your Telegram Media Companion" in first_msg.text

    # 2. Switch UI language to Ukrainian via /settings callback
    settings_msg = _make_message(
        recording_bot, chat_id=user_id, chat_type="private", user_id=user_id, text="/settings", message_id=2
    )
    sessions = await test_container.get(SettingsSessionService)
    context = await sessions.prepare_listing(user_id, settings_msg.chat.id, settings_msg.message_id)
    raw_callback = SettingsCallback(menu=SettingsMenu.UI_LANG, action=SettingsAction.SELECT, value="uk")
    token = await sessions.issue(context, [raw_callback.pack()])
    callback = raw_callback.model_copy(update={"token": token})
    recording_bot.session.responses.extend([True, True])
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=2,
            callback_query=CallbackQuery(
                id="cb-priv-uk",
                from_user=User(id=user_id, is_bot=False, first_name="Tester"),
                chat_instance="synth",
                message=settings_msg,
                data=callback.pack(),
            ).as_(recording_bot),
        ),
    )

    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    edits = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)]
    assert answers[-1].text == "Мову успішно змінено на українську."
    assert edits[-1].text is not None
    assert "Мова інтерфейсу" in edits[-1].text

    # 3. Subsequent /help renders in Ukrainian
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=3,
            message=_make_message(
                recording_bot, chat_id=user_id, chat_type="private", user_id=user_id, text="/help", message_id=3
            ),
        ),
    )
    uk_help = next(c for c in reversed(recording_bot.session.calls) if isinstance(c, SendMessage))
    assert "Ваш медіа-помічник" in uk_help.text


async def test_e2e_group_vs_private_audience_isolation(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Group set to Ukrainian serves Ukrainian to members while private chats retain English."""
    admin_id = 730010
    member_id = 730011
    group_id = -100730012

    users = await test_container.get(UserStore)
    await users.ingest_user(admin_id)
    await users.ingest_user(member_id)
    await provision_role(test_container, admin_id)

    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(group_id)
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await (await test_container.get(GroupGrantStore)).approve(chat.id, ActorContext.cli())
    now = datetime.now(UTC) - timedelta(seconds=1)
    expiry = now + timedelta(minutes=5)
    evidence = MembershipEvidence(group_id, admin_id, MembershipStatus.ADMIN, now, expiry)
    await (await test_container.get(MembershipStore)).save_admins(
        chat.id, AdministratorEvidence(group_id, True, (evidence,), now, expiry), ActorContext.cli()
    )

    dp = await _build_dispatcher(test_container)

    # Admin switches group UI language to Ukrainian
    group_settings_msg = _make_message(
        recording_bot, chat_id=group_id, chat_type="supergroup", user_id=admin_id, text="/settings", message_id=10
    )
    callback = await bind_settings_callback(
        test_container,
        group_settings_msg,
        admin_id,
        SettingsCallback(menu=SettingsMenu.UI_LANG, action=SettingsAction.SELECT, value="uk"),
    )
    recording_bot.session.responses.extend([True, True])
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=10,
            callback_query=CallbackQuery(
                id="cb-grp-uk",
                from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
                chat_instance="synth",
                message=group_settings_msg,
                data=callback.pack(),
            ).as_(recording_bot),
        ),
    )

    # Member triggers /help and /feedback in group -> receives Ukrainian
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=11,
            message=_make_message(
                recording_bot, chat_id=group_id, chat_type="supergroup", user_id=member_id, text="/help", message_id=11
            ),
        ),
    )
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=12,
            message=_make_message(
                recording_bot,
                chat_id=group_id,
                chat_type="supergroup",
                user_id=member_id,
                text="/feedback",
                message_id=12,
            ),
        ),
    )

    group_msgs = [c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == group_id]
    assert "Ваш медіа-помічник" in group_msgs[-2].text
    assert "Як надіслати відгук" in group_msgs[-1].text

    # Same member triggers /help in private chat -> retains default English
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=13,
            message=_make_message(
                recording_bot, chat_id=member_id, chat_type="private", user_id=member_id, text="/help", message_id=13
            ),
        ),
    )
    priv_msgs = [c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == member_id]
    assert "Your Telegram Media Companion" in priv_msgs[-1].text


async def test_e2e_fallback_for_unconfigured_and_unsupported_locales(test_container: AsyncContainer) -> None:
    """Unconfigured entities and unsupported locale codes fall back to English."""
    i18n_manager = await test_container.get(AudienceI18nManager)
    assert await i18n_manager.get_user_locale(999_888_777) == "en"
    assert await i18n_manager.get_chat_locale(-100_999_888_777) is None

    resolved = await i18n_manager.get_locale(
        event_chat=Chat(id=-100_999_888_777, type="supergroup"),
        event_from_user=User(id=999_888_777, is_bot=False, first_name="Fallback"),
    )
    assert resolved == "en"
