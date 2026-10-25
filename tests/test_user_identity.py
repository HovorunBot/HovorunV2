"""Tests for lifecycle-aware user identity records and persistence (Block 007)."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.ports import UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import ActorOrigin, Platform
from hovorunv2.domain.user import UserDB


def test_user_model_has_no_invasive_personal_data_columns() -> None:
    """Verify no phone, biography, contacts, or private profile columns exist on UserDB."""
    column_names = {c.name for c in UserDB.__table__.columns}

    # Forbidden personal data fields
    forbidden_fields = {"phone", "phone_number", "bio", "biography", "contact", "contacts", "email", "full_name"}
    assert column_names.isdisjoint(forbidden_fields)

    # Required operational fields
    expected_fields = {
        "id",
        "platform",
        "telegram_id",
        "role",
        "is_trusted",
        "generation",
        "created_at",
        "updated_at",
        "deleted_at",
        "deleted_by_actor_id",
        "deleted_reason",
    }
    assert expected_fields.issubset(column_names)


@pytest.mark.asyncio
async def test_active_identity_uniqueness_enforced_by_sqlite(test_container: AsyncContainer) -> None:
    """SQLite enforces that only one active record can exist per (platform, telegram_id)."""
    session_maker = await test_container.get(async_sessionmaker)
    user_id = 990001

    # Insert first active user
    async with session_maker() as session:
        user1 = UserDB(telegram_id=user_id, platform=Platform.TELEGRAM, is_trusted=False)
        session.add(user1)
        await session.commit()

    # Attempting to insert a second active user with the same telegram_id fails with IntegrityError
    async with session_maker() as session:
        user2 = UserDB(telegram_id=user_id, platform=Platform.TELEGRAM, is_trusted=True)
        session.add(user2)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_soft_deleted_and_active_generations_can_coexist(test_container: AsyncContainer) -> None:
    """Multiple soft-deleted historical records can coexist with one active generation."""
    session_maker = await test_container.get(async_sessionmaker)
    user_store = await test_container.get(UserStore)
    tg_id = 990002

    # 1. Ingest initial active user (generation 1)
    user = await user_store.ingest_user(tg_id)
    assert user is not None
    assert user.generation == 1

    # 2. Soft-delete generation 1
    actor = ActorContext.cli(actor_id=None, reason="operator reset")
    deleted = await user_store.soft_delete_user(tg_id, actor=actor, reason="operator reset")
    assert deleted is True

    # 3. Insert historical/new generation directly (e.g. recovery workflow)
    async with session_maker() as session:
        gen2 = UserDB(
            telegram_id=tg_id,
            platform=Platform.TELEGRAM,
            is_trusted=False,
            generation=2,
        )
        session.add(gen2)
        await session.commit()

    # 4. Verify historical lookup returns both generations
    history = await user_store.get_historical_by_telegram_id(tg_id)
    assert len(history) == 2
    assert [u.generation for u in history] == [2, 1]

    # Active lookup returns only generation 2
    active = await user_store.get_by_telegram_id(tg_id)
    assert active is not None
    assert active.generation == 2


@pytest.mark.asyncio
async def test_tombstone_aware_ingestion_prevents_silent_resurrection(test_container: AsyncContainer) -> None:
    """Tombstone-aware ingestion does not recreate or resurrect a deleted numeric identity."""
    user_store = await test_container.get(UserStore)
    tg_id = 990003

    # Ingest user
    user = await user_store.ingest_user(tg_id)
    assert user is not None

    # Soft-delete user
    actor = ActorContext.telegram(user_id=123, reason="user requested account deletion")
    assert await user_store.soft_delete_user(tg_id, actor=actor, reason="user requested deletion") is True

    # Normal lookup returns None
    assert await user_store.get_by_telegram_id(tg_id) is None
    assert await user_store.get_by_id(user.id) is None

    # Re-ingesting via message arrival returns None (tombstone recognized, no recreation)
    assert await user_store.ingest_user(tg_id) is None

    # Normal lookup still returns None
    assert await user_store.get_by_telegram_id(tg_id) is None


@pytest.mark.asyncio
async def test_audited_user_lifecycle_mutations(test_container: AsyncContainer) -> None:
    """User creation, trust modification, and soft-deletion are recorded in audit events."""
    session_maker = await test_container.get(async_sessionmaker)
    user_store = await test_container.get(UserStore)
    tg_id = 990004

    # 1. Ingestion creates user and 1 audit event
    actor_tg = ActorContext.telegram(user_id=tg_id, reason="new chat member")
    user = await user_store.ingest_user(tg_id, actor=actor_tg)
    assert user is not None

    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB)
                    .where(AuditEventDB.target_type == "user", AuditEventDB.target_id == str(tg_id))
                    .order_by(AuditEventDB.id.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].action == "create"
        assert events[0].actor_origin == ActorOrigin.TELEGRAM

    # 2. Re-ingesting active user is no-op, produces no extra audit event
    await user_store.ingest_user(tg_id)
    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB).where(AuditEventDB.target_type == "user", AuditEventDB.target_id == str(tg_id))
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1

    # 3. Set trust creates audit event
    cli_actor = ActorContext.cli(actor_id=None, reason="manual verification")
    assert await user_store.set_trust(tg_id, is_trusted=True, actor=cli_actor) is True

    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB)
                    .where(AuditEventDB.target_type == "user", AuditEventDB.target_id == str(tg_id))
                    .order_by(AuditEventDB.id.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 2
        assert events[1].action == "set_trust"
        assert events[1].changed_fields == {"is_trusted": True}

    # 4. Soft-delete creates audit event
    del_actor = ActorContext.cli(actor_id=None, reason="cleanup test")
    assert await user_store.soft_delete_user(tg_id, actor=del_actor, reason="cleanup test") is True

    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB)
                    .where(AuditEventDB.target_type == "user", AuditEventDB.target_id == str(tg_id))
                    .order_by(AuditEventDB.id.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 3
        assert events[2].action == "delete"
        assert events[2].changed_fields is not None
        assert events[2].changed_fields["reason"] == "cleanup test"
