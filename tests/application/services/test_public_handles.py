"""Tests for public username resolution, case-insensitive uniqueness, and grant preservation."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import Platform, TargetKind
from hovorunv2.domain.handle import PublicHandleDB, normalize_handle, validate_handle


def _public_handle_indexes(connection: Connection) -> dict[str, dict[str, object]]:
    """Reflect the public-handle indexes inside a synchronous run_sync callback."""
    return {index["name"]: index for index in inspect(connection).get_indexes("public_handles")}


def test_normalize_and_validate_handle() -> None:
    """Handle normalization strips @, whitespace, and lowercases; validation enforces chars."""
    assert normalize_handle("@Alice_Bot") == "alice_bot"
    assert normalize_handle("  Bob  ") == "bob"
    assert normalize_handle("@") is None
    assert normalize_handle("") is None
    assert normalize_handle(None) is None

    assert validate_handle("alice") is True
    assert validate_handle("@Alice_123") is True
    assert validate_handle("invalid space") is False
    assert validate_handle("invalid!char") is False


async def test_case_insensitive_uniqueness_enforced_by_database(test_container: AsyncContainer) -> None:
    """Duplicate handles differing only by case fail database uniqueness constraint."""
    session_maker = await test_container.get(async_sessionmaker)

    async with session_maker() as session:
        h1 = PublicHandleDB(
            handle_normalized="charlie",
            handle_display="Charlie",
            target_kind=TargetKind.USER,
            target_id=1001,
            platform=Platform.TELEGRAM,
        )
        session.add(h1)
        await session.commit()

    # Attempt to insert same normalized handle with different display casing
    async with session_maker() as session:
        h2 = PublicHandleDB(
            handle_normalized="charlie",
            handle_display="CHARLIE",
            target_kind=TargetKind.GROUP,
            target_id=-100200300,
            platform=Platform.TELEGRAM,
        )
        session.add(h2)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_multiple_absent_usernames_work(test_container: AsyncContainer) -> None:
    """Entities without usernames have no public_handles rows and co-exist safely."""
    handle_store = await test_container.get(PublicHandleStore)

    # Looking up non-existent handles returns None (no synthetic placeholders)
    res1 = await handle_store.resolve_handle("@user_without_handle")
    res2 = await handle_store.resolve_handle("@another_absent_handle")
    assert res1 is None
    assert res2 is None


async def test_rename_and_reuse_does_not_transfer_trust_or_grants(test_container: AsyncContainer) -> None:
    """When target A relinquishes a handle and target B claims it, target B gains no grants."""
    user_store = await test_container.get(UserStore)
    handle_store = await test_container.get(PublicHandleStore)

    actor = ActorContext.cli()
    alice_id = 770011
    bob_id = 770022

    # Ingest Alice and grant trust
    user_alice = await user_store.ingest_user(alice_id, actor=actor)
    assert user_alice is not None
    await user_store.set_trust(alice_id, is_trusted=True, actor=actor)

    # Ingest Bob (untrusted)
    user_bob = await user_store.ingest_user(bob_id, actor=actor)
    assert user_bob is not None
    assert user_bob.is_trusted is False

    # Alice syncs handle @speedy
    await handle_store.sync_handle(TargetKind.USER, alice_id, "@Speedy", actor=actor)

    # Alice changes handle to @alice_renamed, freeing @speedy
    await handle_store.sync_handle(TargetKind.USER, alice_id, "@Alice_Renamed", actor=actor)

    # Bob now claims @speedy
    await handle_store.sync_handle(TargetKind.USER, bob_id, "@Speedy", actor=actor)

    # Verify handle @speedy resolves to Bob's numeric ID
    resolved = await handle_store.resolve_handle("@speedy")
    assert resolved is not None
    assert resolved.target_kind == TargetKind.USER
    assert resolved.target_id == bob_id

    # Verify Bob is STILL untrusted (grants did not transfer!)
    bob_db = await user_store.get_by_telegram_id(bob_id)
    assert bob_db is not None
    assert bob_db.is_trusted is False

    # Verify Alice is STILL trusted
    alice_db = await user_store.get_by_telegram_id(alice_id)
    assert alice_db is not None
    assert alice_db.is_trusted is True


async def test_soft_deletion_awareness_and_audit(test_container: AsyncContainer) -> None:
    """Soft-deleted targets are excluded from handle resolution, and mutations are audited."""
    user_store = await test_container.get(UserStore)
    handle_store = await test_container.get(PublicHandleStore)
    session_maker = await test_container.get(async_sessionmaker)

    actor = ActorContext(origin="telegram", actor_id=99999)
    target_id = 888123

    # Ingest user and assign handle
    await user_store.ingest_user(target_id, actor=actor)
    await handle_store.sync_handle(TargetKind.USER, target_id, "@DeletableUser", actor=actor)

    # Resolve active handle
    resolved = await handle_store.resolve_handle("@deletableuser")
    assert resolved is not None
    assert resolved.target_id == target_id

    # Soft-delete the user
    await user_store.soft_delete_user(target_id, actor=actor, reason="user departed")

    # Soft-deleted user handle can no longer be resolved
    resolved_after_delete = await handle_store.resolve_handle("@deletableuser")
    assert resolved_after_delete is None

    # Check audit log for handle sync mutation
    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type == "public_handle",
                        AuditEventDB.target_id == f"{TargetKind.USER}:{target_id}",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) >= 1
        assert events[0].action == "sync"
        assert events[0].changed_fields is not None
        assert "handle" in events[0].changed_fields


async def test_stale_handle_reassignment_on_fresh_evidence(test_container: AsyncContainer) -> None:
    """When target B provides authenticated evidence for a handle held by target A, handle moves."""
    user_store = await test_container.get(UserStore)
    handle_store = await test_container.get(PublicHandleStore)

    actor = ActorContext(origin="telegram", actor_id=111)
    user_1 = 111111
    user_2 = 222222

    await user_store.ingest_user(user_1, actor=actor)
    await user_store.ingest_user(user_2, actor=actor)

    # User 1 registers handle
    await handle_store.sync_handle(TargetKind.USER, user_1, "@SharedHandle", actor=actor)
    res1 = await handle_store.resolve_handle("@SharedHandle")
    assert res1 is not None
    assert res1.target_id == user_1

    # User 2 presents fresh evidence for @SharedHandle
    await handle_store.sync_handle(TargetKind.USER, user_2, "@SharedHandle", actor=actor)
    res2 = await handle_store.resolve_handle("@SharedHandle")
    assert res2 is not None
    assert res2.target_id == user_2

    # User 1 no longer has handle
    handle_u1 = await handle_store.get_handle_by_target(TargetKind.USER, user_1)
    assert handle_u1 is None


def test_public_handle_model_has_no_invasive_personal_data_columns() -> None:
    """Verify no phone, biography, contacts, or private profile columns exist on PublicHandleDB."""
    column_names = {c.name for c in PublicHandleDB.__table__.columns}
    forbidden_fields = {"phone", "phone_number", "bio", "biography", "contact", "contacts", "email", "full_name"}
    assert column_names.isdisjoint(forbidden_fields)

    expected_fields = {
        "id",
        "handle_normalized",
        "handle_display",
        "target_kind",
        "target_id",
        "platform",
        "created_at",
        "updated_at",
    }
    assert expected_fields.issubset(column_names)


async def test_public_handles_schema_has_expected_unique_indexes(test_engine: AsyncEngine) -> None:
    """Verify the PostgreSQL target schema owns both public-handle uniqueness indexes."""
    async with test_engine.connect() as connection:
        indexes = await connection.run_sync(_public_handle_indexes)

    expected = {
        "uq_public_handles_handle": ("platform", "handle_normalized"),
        "uq_public_handles_target": ("platform", "target_kind", "target_id"),
    }
    for name, columns in expected.items():
        assert indexes[name]["unique"] is True
        assert tuple(indexes[name]["column_names"]) == columns


async def test_public_handles_target_uniqueness_enforced_by_postgresql(test_container: AsyncContainer) -> None:
    """PostgreSQL rejects a second handle for the same target identity."""
    session_maker = await test_container.get(async_sessionmaker)

    async with session_maker() as session:
        session.add(
            PublicHandleDB(
                handle_normalized="firsthandle",
                handle_display="FirstHandle",
                target_kind=TargetKind.USER,
                target_id=12345,
                platform=Platform.TELEGRAM,
            )
        )
        await session.commit()

    async with session_maker() as session:
        session.add(
            PublicHandleDB(
                handle_normalized="secondhandle",
                handle_display="SecondHandle",
                target_kind=TargetKind.USER,
                target_id=12345,
                platform=Platform.TELEGRAM,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
