"""Verify UI locale persistence on users and chats against real PostgreSQL."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository


@pytest.mark.asyncio
async def test_user_ui_locale_persistence_and_update(test_container: AsyncContainer) -> None:
    """Verify UserDB ui_locale defaults to None, updates to a supported code, and resets to None."""
    session_factory = await test_container.get(async_sessionmaker)
    telegram_id = 999_888_777

    async with session_factory() as session:
        repo = SQLAlchemyUserRepository(session)
        user, created = await repo.ingest(telegram_id)
        assert created is True
        assert user is not None
        assert user.ui_locale is None
        await session.commit()

    async with session_factory() as session:
        repo = SQLAlchemyUserRepository(session)
        updated = await repo.set_ui_locale(telegram_id, "uk")
        assert updated is True
        await session.commit()

    async with session_factory() as session:
        user = await session.scalar(select(UserDB).where(UserDB.telegram_id == telegram_id))
        assert user is not None
        assert user.ui_locale == "uk"

        repo = SQLAlchemyUserRepository(session)
        cleared = await repo.set_ui_locale(telegram_id, None)
        assert cleared is True
        await session.commit()

    async with session_factory() as session:
        user = await session.scalar(select(UserDB).where(UserDB.telegram_id == telegram_id))
        assert user is not None
        assert user.ui_locale is None


@pytest.mark.asyncio
async def test_chat_ui_locale_persistence_and_update(test_container: AsyncContainer) -> None:
    """Verify ChatDB ui_locale defaults to None, updates to a supported code, and resets to None."""
    session_factory = await test_container.get(async_sessionmaker)
    chat_id = -100_123_456_789

    async with session_factory() as session:
        chat = ChatDB(
            chat_id=chat_id,
            platform=Platform.TELEGRAM,
            status=ChatStatus.APPROVED,
            chat_type="supergroup",
            title="Test Group",
        )
        session.add(chat)
        await session.commit()

    async with session_factory() as session:
        repo = SQLAlchemyChatRepository(session)
        loaded = await repo.get_by_id(chat_id)
        assert loaded is not None
        assert loaded.ui_locale is None

        updated = await repo.set_ui_locale(chat_id, "uk")
        assert updated is True
        await session.commit()

    async with session_factory() as session:
        repo = SQLAlchemyChatRepository(session)
        loaded = await repo.get_by_id(chat_id)
        assert loaded is not None
        assert loaded.ui_locale == "uk"

        cleared = await repo.set_ui_locale(chat_id, None)
        assert cleared is True
        await session.commit()

    async with session_factory() as session:
        repo = SQLAlchemyChatRepository(session)
        loaded = await repo.get_by_id(chat_id)
        assert loaded is not None
        assert loaded.ui_locale is None


@pytest.mark.asyncio
async def test_set_ui_locale_on_nonexistent_identity(test_container: AsyncContainer) -> None:
    """Verify attempting to update ui_locale on missing user or chat returns False."""
    session_factory = await test_container.get(async_sessionmaker)

    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        assert await user_repo.set_ui_locale(999_999_999, "uk") is False

        chat_repo = SQLAlchemyChatRepository(session)
        assert await chat_repo.set_ui_locale(-999_999_999, "uk") is False
