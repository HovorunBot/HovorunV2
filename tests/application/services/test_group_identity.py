"""Tests for extended group/chat identity, lifecycle uniqueness, and migration safety."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.chat import ChatDB, chat_commands
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import ChatStatus, Platform


async def test_active_chat_uniqueness_enforced_by_postgresql(test_container: AsyncContainer) -> None:
    """PostgreSQL enforces that only one active record can exist per (platform, chat_id)."""
    session_maker = await test_container.get(async_sessionmaker)
    chat_id = -1001234567890

    # Insert first active group
    async with session_maker() as session:
        chat1 = ChatDB(chat_id=chat_id, platform=Platform.TELEGRAM, status=ChatStatus.APPROVED)
        session.add(chat1)
        await session.commit()

    # Attempting to insert a second active record with the same chat_id fails
    async with session_maker() as session:
        chat2 = ChatDB(chat_id=chat_id, platform=Platform.TELEGRAM, status=ChatStatus.UNAUTHORIZED)
        session.add(chat2)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_soft_deleted_chat_and_active_coexist(test_container: AsyncContainer) -> None:
    """Soft-deleted chats and active chat generations can coexist."""
    session_maker = await test_container.get(async_sessionmaker)
    chat_store = await test_container.get(ChatStore)
    chat_id = -1009876543210

    # 1. Create chat
    chat = await chat_store.get_or_create_chat(chat_id)
    assert chat is not None
    assert chat.generation == 1

    # 2. Soft-delete generation 1
    actor = ActorContext.cli(actor_id=None, reason="group reset")
    deleted = await chat_store.soft_delete_chat(chat_id, actor=actor, reason="group reset")
    assert deleted is True

    # 3. Normal lookup excludes soft-deleted chat
    assert await chat_store.get_chat(chat_id) is None

    # 4. Insert generation 2 directly
    async with session_maker() as session:
        gen2 = ChatDB(
            chat_id=chat_id,
            platform=Platform.TELEGRAM,
            status=ChatStatus.APPROVED,
            generation=2,
        )
        session.add(gen2)
        await session.commit()

    # 5. Historical lookup returns both generations
    history = await chat_store.get_historical_by_chat_id(chat_id)
    assert len(history) == 2
    assert [c.generation for c in history] == [2, 1]

    # Active lookup returns only generation 2
    active = await chat_store.get_chat(chat_id)
    assert active is not None
    assert active.generation == 2


async def test_soft_delete_is_audited_transactionally(test_container: AsyncContainer) -> None:
    """Soft-deleting a chat records an audit event in the same transaction."""
    session_maker = await test_container.get(async_sessionmaker)
    chat_store = await test_container.get(ChatStore)
    chat_id = -1005544332211

    await chat_store.get_or_create_chat(chat_id)

    del_actor = ActorContext.telegram(user_id=12345, reason="group decommissioned")
    assert await chat_store.soft_delete_chat(chat_id, actor=del_actor, reason="group decommissioned") is True

    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type == "chat",
                        AuditEventDB.target_id == str(chat_id),
                        AuditEventDB.action == "delete",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].reason == "group decommissioned"
        assert events[0].changed_fields is not None
        assert events[0].changed_fields["reason"] == "group decommissioned"


async def test_foreign_key_enforcement_on_chat_associations(test_container: AsyncContainer) -> None:
    """PostgreSQL enforces foreign keys on chat command associations."""
    session_maker = await test_container.get(async_sessionmaker)

    # Attempting to associate an invalid chat_id (that doesn't exist in chats) fails with IntegrityError
    async with session_maker() as session:
        # Fetch an existing seeded command
        cmd = (await session.execute(select(CommandDB))).scalars().first()
        assert cmd is not None

        stmt = chat_commands.insert().values(chat_id=9999999, command_id=cmd.id)
        with pytest.raises(IntegrityError):
            await session.execute(stmt)


async def test_migration_preserves_chat_data_byte_for_byte(test_container: AsyncContainer) -> None:
    """Existing chat row fields (salts, language, commands) are preserved accurately."""
    session_maker = await test_container.get(async_sessionmaker)
    chat_id = -1007788990011
    salt_val = "preserved_salt_material_xyz_123"

    async with session_maker() as session:
        chat = ChatDB(
            chat_id=chat_id,
            platform=Platform.TELEGRAM,
            status=ChatStatus.APPROVED,
            target_lang="uk",
            ignored_langs=["en", "pl"],
            encryption_salt=salt_val,
            title="My Preserved Group",
            chat_type="supergroup",
            is_bot_present=True,
            generation=1,
        )
        session.add(chat)
        await session.commit()

    async with session_maker() as session:
        stmt = select(ChatDB).where(ChatDB.chat_id == chat_id)
        row = (await session.execute(stmt)).scalar_one()

        assert row.chat_id == chat_id
        assert row.status == ChatStatus.APPROVED
        assert row.target_lang == "uk"
        assert row.ignored_langs == ["en", "pl"]
        assert row.encryption_salt == salt_val
        assert row.title == "My Preserved Group"
        assert row.chat_type == "supergroup"
        assert row.is_bot_present is True
        assert row.deleted_at is None
