"""Behavioral tests for localized primary bot interactive journeys."""

from datetime import UTC, datetime

from aiogram import Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User
from aiogram_i18n import I18nMiddleware
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.messaging.feedback_service import FeedbackService
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform, Role
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository
from hovorunv2.interface.telegram.handlers.feedback import FeedbackCommand
from hovorunv2.interface.telegram.handlers.help import HelpCommand
from hovorunv2.interface.telegram.i18n import AudienceI18nManager, create_i18n_core
from hovorunv2.interface.telegram.settings.prompts import (
    TelegramUnauthorizedPromptSender,
    build_unauthorized_prompt,
)
from tests.conftest import provision_role
from tests.utils.telegram.session import RecordingBot


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
        chat=Chat(id=chat_id, type=chat_type, title="Test Group" if chat_type != "private" else None),
        from_user=User(id=user_id, is_bot=False, first_name="Tester"),
        text=text,
    ).as_(bot)


async def test_start_and_help_localized_via_middleware(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify /start and /help render in Ukrainian and English while keeping commands unlocalized."""
    session_factory = await test_container.get(async_sessionmaker)
    uk_user_id = 710001
    en_user_id = 710002

    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        uk_user, _ = await user_repo.ingest(uk_user_id)
        assert uk_user is not None
        uk_user.ui_locale = "uk"
        en_user, _ = await user_repo.ingest(en_user_id)
        assert en_user is not None
        en_user.ui_locale = "en"
        await session.commit()

    settings = await test_container.get(AppSettings)
    access_service = await test_container.get(AccessService)
    help_cmd = HelpCommand(settings, access_service)

    dp = Dispatcher()
    dp.message.register(help_cmd.handle, help_cmd.trigger)
    middleware = I18nMiddleware(
        core=create_i18n_core(load_now=True),
        manager=AudienceI18nManager(session_maker=session_factory, default_locale="en"),
    )
    middleware.setup(dp)

    await dp.feed_update(
        recording_bot,
        Update(
            update_id=1,
            message=_make_message(
                recording_bot, chat_id=uk_user_id, chat_type="private", user_id=uk_user_id, text="/start"
            ),
        ),
    )
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=2,
            message=_make_message(
                recording_bot, chat_id=en_user_id, chat_type="private", user_id=en_user_id, text="/help", message_id=2
            ),
        ),
    )

    sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert len(sent) == 2

    uk_text = sent[0].text
    assert "Ваш медіа-помічник" in uk_text
    assert "/allow_chat" in uk_text
    assert "/feedback" in uk_text
    assert "/settings" in uk_text

    en_text = sent[1].text
    assert "Your Telegram Media Companion" in en_text
    assert "/allow_chat" in en_text
    assert "/feedback" in en_text
    assert "/settings" in en_text


async def test_help_text_escapes_dynamic_html_parameters(test_container: AsyncContainer) -> None:
    """Verify dynamic parameters interpolated into HTML help text are escaped."""
    settings = await test_container.get(AppSettings)
    access_service = await test_container.get(AccessService)
    assert isinstance(settings, Settings)
    unsafe_settings = settings.model_copy(update={"project_description": "<script>alert('xss')</script> & friends"})
    help_cmd = HelpCommand(unsafe_settings, access_service)

    uk_text = help_cmd.get_help_text(locale="uk")
    en_text = help_cmd.get_help_text(locale="en")

    for text in (uk_text, en_text):
        assert "<script>" not in text
        assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt; &amp; friends" in text


async def test_feedback_localized_via_middleware(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify /feedback instructions, delivery notice, and cooldown render in user's locale."""
    await provision_role(test_container, 710999, Role.OWNER)
    session_factory = await test_container.get(async_sessionmaker)
    uk_user_id = 710011

    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        uk_user, _ = await user_repo.ingest(uk_user_id)
        assert uk_user is not None
        uk_user.ui_locale = "uk"
        await session.commit()

    owner_notifier = await test_container.get(OwnerNotifier)
    cache = await test_container.get(CachePort)
    feedback_cmd = FeedbackCommand(FeedbackService(owner_notifier), cache)

    dp = Dispatcher()
    dp.message.register(feedback_cmd.handle, feedback_cmd.trigger)
    middleware = I18nMiddleware(
        core=create_i18n_core(load_now=True),
        manager=AudienceI18nManager(session_maker=session_factory, default_locale="en"),
    )
    middleware.setup(dp)

    # Empty /feedback -> instruction
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=10,
            message=_make_message(
                recording_bot,
                chat_id=uk_user_id,
                chat_type="private",
                user_id=uk_user_id,
                text="/feedback",
                message_id=10,
            ),
        ),
    )
    # Valid /feedback -> delivered
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=11,
            message=_make_message(
                recording_bot,
                chat_id=uk_user_id,
                chat_type="private",
                user_id=uk_user_id,
                text="/feedback Чудовий бот!",
                message_id=11,
            ),
        ),
    )
    # Repeat /feedback -> cooldown
    await dp.feed_update(
        recording_bot,
        Update(
            update_id=12,
            message=_make_message(
                recording_bot,
                chat_id=uk_user_id,
                chat_type="private",
                user_id=uk_user_id,
                text="/feedback Ще раз",
                message_id=12,
            ),
        ),
    )

    user_replies = [c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == uk_user_id]
    assert len(user_replies) == 3
    assert "Як надіслати відгук" in user_replies[0].text
    assert "/feedback" in user_replies[0].text
    assert "Дякуємо!" in user_replies[1].text
    assert "зачекайте" in user_replies[2].text.lower()


async def test_unauthorized_prompt_uses_chat_locale(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify unauthorized prompt builder and sender localize using chat's ui_locale."""
    en_text, en_builder = build_unauthorized_prompt(-1001, 1, 0, locale="en")
    uk_text, uk_builder = build_unauthorized_prompt(-1001, 1, 0, locale="uk")

    assert "Chat Authorization Required" in en_text
    assert "Activate / Request Access" in en_builder.export()[0][0].text
    assert "Потрібна авторизація чату" in uk_text
    assert "Активувати / Запросити доступ" in uk_builder.export()[0][0].text

    session_factory = await test_container.get(async_sessionmaker)
    group_id = -100710055
    async with session_factory() as session:
        session.add(
            ChatDB(
                chat_id=group_id,
                platform=Platform.TELEGRAM,
                status=ChatStatus.UNAUTHORIZED,
                chat_type="supergroup",
                ui_locale="uk",
            )
        )
        await session.commit()

    chat_store = await test_container.get(ChatStore)
    sender = TelegramUnauthorizedPromptSender(chat_store)
    sent_ok = await sender.send(recording_bot, group_id)
    assert sent_ok is True

    sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == group_id]
    assert len(sent) == 1
    assert "Потрібна авторизація чату" in sent[0].text
    assert "/allow_chat" in sent[0].text
    assert "/feedback" in sent[0].text
