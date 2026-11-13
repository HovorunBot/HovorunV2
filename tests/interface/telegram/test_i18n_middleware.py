"""Integration and unit tests for audience-aware i18n manager and middleware."""

from datetime import UTC, datetime

import pytest
from aiogram import Dispatcher
from aiogram.types import Chat, Message, Update, User
from aiogram_i18n import I18nContext, I18nMiddleware
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform
from hovorunv2.infrastructure.persistence.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository
from hovorunv2.interface.telegram.i18n import AudienceI18nManager, create_i18n_core
from tests.utils.telegram.session import RecordingBot


@pytest.mark.asyncio
async def test_audience_manager_resolves_group_vs_private(test_container: AsyncContainer) -> None:
    """Verify group chat resolves chat locale and private chat resolves user locale."""
    session_factory = await test_container.get(async_sessionmaker)
    manager = AudienceI18nManager(session_maker=session_factory, default_locale="en")

    group_chat_id = -100_555_444_333
    user_id = 111_222_333

    # Seed group chat with 'uk' locale
    async with session_factory() as session:
        chat = ChatDB(
            chat_id=group_chat_id,
            platform=Platform.TELEGRAM,
            status=ChatStatus.APPROVED,
            chat_type="supergroup",
            ui_locale="uk",
        )
        session.add(chat)
        await session.commit()

    # Seed user with 'en' locale
    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        user_db, _ = await user_repo.ingest(user_id)
        assert user_db is not None
        user_db.ui_locale = "en"
        await session.commit()

    tg_group_chat = Chat(id=group_chat_id, type="supergroup", title="Test Group")
    tg_user = User(id=user_id, is_bot=False, first_name="Test")
    tg_private_chat = Chat(id=user_id, type="private")

    # In group interaction: group locale 'uk' wins over user's 'en'
    resolved_group = await manager.get_locale(event_chat=tg_group_chat, event_from_user=tg_user)
    assert resolved_group == "uk"

    # In private interaction: user locale 'en' is resolved
    resolved_private = await manager.get_locale(event_chat=tg_private_chat, event_from_user=tg_user)
    assert resolved_private == "en"

    # In group interaction, get_user_locale still retrieves user's personal preference
    assert await manager.get_user_locale(user_id) == "en"


@pytest.mark.asyncio
async def test_audience_manager_defaults_when_unset(test_container: AsyncContainer) -> None:
    """Verify unset preferences fall back to default English locale."""
    session_factory = await test_container.get(async_sessionmaker)
    manager = AudienceI18nManager(session_maker=session_factory, default_locale="en")

    unknown_chat = Chat(id=-100_999_888, type="supergroup")
    unknown_user = User(id=888_777_666, is_bot=False, first_name="Stranger")

    assert await manager.get_locale(event_chat=unknown_chat, event_from_user=unknown_user) == "en"
    assert await manager.get_locale(event_chat=None, event_from_user=None) == "en"


@pytest.mark.asyncio
async def test_audience_manager_set_locale_persists(test_container: AsyncContainer) -> None:
    """Verify manager.set_locale persists to respective ChatDB and UserDB records."""
    session_factory = await test_container.get(async_sessionmaker)
    manager = AudienceI18nManager(session_maker=session_factory, default_locale="en")

    group_id = -100_777_888_999
    user_id = 333_444_555

    # Seed entities with None
    async with session_factory() as session:
        session.add(
            ChatDB(
                chat_id=group_id,
                platform=Platform.TELEGRAM,
                status=ChatStatus.APPROVED,
                chat_type="group",
            )
        )
        user_repo = SQLAlchemyUserRepository(session)
        await user_repo.ingest(user_id)
        await session.commit()

    tg_group = Chat(id=group_id, type="group")
    tg_user = User(id=user_id, is_bot=False, first_name="User")

    await manager.set_locale("uk", event_chat=tg_group)
    await manager.set_locale("uk", event_chat=Chat(id=user_id, type="private"), event_from_user=tg_user)

    async with session_factory() as session:
        chat_repo = SQLAlchemyChatRepository(session)
        chat = await chat_repo.get_by_id(group_id)
        assert chat is not None
        assert chat.ui_locale == "uk"

        user_repo = SQLAlchemyUserRepository(session)
        user = await user_repo.get_by_telegram_id(user_id)
        assert user is not None
        assert user.ui_locale == "uk"


@pytest.mark.asyncio
async def test_i18n_middleware_injects_context(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify I18nMiddleware correctly injects I18nContext into handler execution."""
    session_factory = await test_container.get(async_sessionmaker)
    core = create_i18n_core(load_now=True)
    manager = AudienceI18nManager(session_maker=session_factory, default_locale="en")
    middleware = I18nMiddleware(core=core, manager=manager)

    group_id = -100_888_999_111
    async with session_factory() as session:
        session.add(
            ChatDB(
                chat_id=group_id,
                platform=Platform.TELEGRAM,
                status=ChatStatus.APPROVED,
                chat_type="supergroup",
                ui_locale="uk",
            )
        )
        await session.commit()

    captured_locale: str | None = None
    captured_message: str | None = None

    dp = Dispatcher()
    middleware.setup(dp)

    @dp.message()
    async def handler(message: Message, i18n: I18nContext) -> None:
        nonlocal captured_locale, captured_message
        captured_locale = i18n.locale
        captured_message = i18n.get("welcome")

    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            date=datetime.now(UTC),
            chat=Chat(id=group_id, type="supergroup", title="Ukrainian Group"),
            from_user=User(id=123, is_bot=False, first_name="TestUser"),
            text="/test",
        ),
    )

    await dp.feed_update(bot=recording_bot, update=update)

    assert captured_locale == "uk"
    assert captured_message == "Ласкаво просимо до Говоруна!"
