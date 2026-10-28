"""Real SQLite routing migration preserves configuration and fails closed on conflicts."""

from datetime import UTC, datetime

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.activation import ActivationAction, ActivationOutcome
from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.crypto import decrypt_payload, encrypt_payload, get_fernet_for_chat
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.group_migration import GroupMigrationStore
from hovorunv2.application.identity import GroupIdentityStore
from hovorunv2.application.permissions import PermissionTarget
from hovorunv2.application.ports import ChatStore, PublicHandleStore, UserStore
from hovorunv2.application.services.activation_service import GroupActivationService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.domain.group_grant import RequestStatus
from hovorunv2.domain.group_route import GroupRouteDB
from hovorunv2.domain.handle import PublicHandleDB
from hovorunv2.interface.telegram.identity import IdentityMiddleware
from tests.conftest import provision_role
from tests.test_chat_status_state_machine import bot_event
from tests.utils.activation import ACTOR
from tests.utils.postgres import reject_audit

OLD = -250001
NEW = -100250001
SALT = "01" * 16


async def test_migration_keeps_identity_configuration_bans_and_encryption(test_container: AsyncContainer) -> None:
    """Keep internal references, exact salt and restrictions across idempotent routing changes."""
    chats = await test_container.get(ChatStore)
    original = await chats.get_or_create_chat(OLD)
    await chats.update_status(OLD, ChatStatus.APPROVED)
    await chats.update_translation_settings(OLD, "uk", ["en"])
    await chats.set_encryption_salt(OLD, SALT)
    grants = await test_container.get(GroupGrantStore)
    await grants.approve(original.id, ActorContext.cli())
    bans = await test_container.get(BanStore)
    await bans.set_ban(
        TargetKind.GROUP, OLD, BanKind.FULL, active=True, actor=ActorContext.cli(reason="Synthetic restriction")
    )
    handles = await test_container.get(PublicHandleStore)
    await handles.sync_handle(TargetKind.GROUP, OLD, "synthetic_group", ActorContext.cli())
    encrypted = encrypt_payload("synthetic encrypted content", get_fernet_for_chat("synthetic-key", SALT))
    migrations = await test_container.get(GroupMigrationStore)
    assert await migrations.migrate(OLD, NEW)
    assert not await migrations.migrate(OLD, NEW)
    current = await chats.get_chat(NEW)
    assert current is not None
    assert current.id == original.id
    assert current.generation == original.generation
    assert current.target_lang == "uk"
    assert current.ignored_langs == ["en"]
    assert current.encryption_salt == SALT
    assert (
        decrypt_payload(encrypted, get_fernet_for_chat("synthetic-key", current.encryption_salt))
        == "synthetic encrypted content"
    )
    assert (await chats.get_chat(OLD)).id == original.id
    assert (await chats.get_or_create_chat(OLD)).id == original.id
    assert await migrations.canonical_id(OLD) == NEW
    assert await bans.list_active(TargetKind.GROUP, NEW)
    assert not (await (await test_container.get(ChatStatusService)).evaluate_access(OLD)).allowed
    assert not (await (await test_container.get(ChatStatusService)).evaluate_access(NEW)).allowed
    assert len(await grants.approvals(original.id)) == 1
    assert (await handles.resolve_handle("synthetic_group")).target_id == NEW
    observed = await (await test_container.get(GroupIdentityStore)).ingest_group(OLD, "Stale title", "group")
    assert observed is not None
    assert observed.chat_id == NEW
    assert observed.chat_type == "supergroup"
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ChatDB)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEventDB).where(AuditEventDB.action == "migrate_route")
            )
            == 1
        )
    await chats.soft_delete_chat(NEW, ActorContext.cli(), "Synthetic deletion")
    assert await migrations.canonical_id(OLD) is None
    assert await (await test_container.get(GroupIdentityStore)).ingest_group(OLD, "Stale", "group") is None


@pytest.mark.parametrize("deleted", [False, True])
async def test_migration_rejects_target_identity_collisions(test_container: AsyncContainer, deleted: bool) -> None:
    """Reject active and tombstoned targets without merging grants or identities."""
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(OLD)
    await chats.get_or_create_chat(NEW)
    if deleted:
        await chats.soft_delete_chat(NEW, ActorContext.cli(), "Synthetic tombstone")
    with pytest.raises(ValueError, match="conflicts"):
        await (await test_container.get(GroupMigrationStore)).migrate(OLD, NEW)
    assert (await chats.get_chat(OLD)).chat_id == OLD


async def test_migration_rolls_back_when_audit_fails(test_container: AsyncContainer) -> None:
    """Roll back routing and aliases if the audit insertion is rejected."""
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(OLD)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        await reject_audit(session, "reject_migration_audit", "NEW.action = 'migrate_route'", "synthetic audit failure")
    with pytest.raises(IntegrityError, match="synthetic audit failure"):
        await (await test_container.get(GroupMigrationStore)).migrate(OLD, NEW)
    assert (await chats.get_chat(OLD)).chat_id == OLD
    assert await chats.get_chat(NEW) is None
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(GroupRouteDB)) == 0


async def test_service_events_migrate_before_identity_ingestion(test_container: AsyncContainer) -> None:
    """Consume both service-event directions before duplicate destination provisioning."""
    chats = await test_container.get(ChatStore)
    original = await chats.get_or_create_chat(OLD)
    middleware = await test_container.get(IdentityMiddleware)
    for chat_id, kind, fields in (
        (OLD, "group", {"migrate_to_chat_id": NEW}),
        (NEW, "supergroup", {"migrate_from_chat_id": OLD}),
    ):
        message = Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=chat_id, type=kind), **fields)
        await middleware(unreachable_handler, message, {})
    assert (await chats.get_chat(NEW)).id == original.id


async def unreachable_handler(_event: object, _data: object) -> None:
    """Fail if a migration event reaches normal feature processing."""
    pytest.fail("Migration event reached feature handler")


async def test_migration_rejects_conflicting_handle_target(test_container: AsyncContainer) -> None:
    """Reject dangling target aliases instead of stealing another public mapping."""
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(OLD)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        session.add(
            PublicHandleDB(
                handle_normalized="conflicting_group",
                handle_display="conflicting_group",
                target_kind=TargetKind.GROUP,
                target_id=NEW,
            )
        )
    with pytest.raises(ValueError, match="public handle"):
        await (await test_container.get(GroupMigrationStore)).migrate(OLD, NEW)
    assert (await chats.get_chat(OLD)).chat_id == OLD


async def test_stale_callbacks_and_membership_cannot_reclaim_handle(test_container: AsyncContainer) -> None:
    """Reject every stale group transport before public handle synchronization or dispatch."""
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(OLD)
    handles = await test_container.get(PublicHandleStore)
    await handles.sync_handle(TargetKind.GROUP, OLD, "synthetic_route", ActorContext.cli())
    await (await test_container.get(GroupMigrationStore)).migrate(OLD, NEW)
    middleware = await test_container.get(IdentityMiddleware)
    chat = Chat(id=OLD, type="group", username="synthetic_route")
    message = Message(message_id=2, date=datetime.now(UTC), chat=chat)
    query = CallbackQuery(
        id="stale-route",
        from_user=User(id=ACTOR, is_bot=False, first_name="Synthetic"),
        chat_instance="test",
        message=message,
    )
    member = bot_event(OLD, present=True, date=datetime.now(UTC)).model_copy(update={"chat": chat})
    for event in (query, member):
        await middleware(unreachable_handler, event, {})
        handle = await handles.resolve_handle("synthetic_route")
        assert handle is not None
        assert handle.target_id == NEW


async def test_activation_rejects_stale_numeric_target_after_migration(test_container: AsyncContainer) -> None:
    """A previously selected group cannot mutate activation using its retired Telegram ID."""
    chats = await test_container.get(ChatStore)
    group = await chats.get_or_create_chat(OLD)
    users = await test_container.get(UserStore)
    await users.ingest_user(ACTOR)
    await users.set_trust(ACTOR, is_trusted=True, actor=ActorContext.cli())
    await (await test_container.get(GroupMigrationStore)).migrate(OLD, NEW)
    service = await test_container.get(GroupActivationService)
    result = await service.enable(ACTOR, PermissionTarget("group", group.id, group.generation, OLD), "group")
    assert result.outcome == ActivationOutcome.UNAVAILABLE
    assert await (await test_container.get(GroupGrantStore)).approvals(group.id) == []


async def test_private_approval_preserves_pending_request_on_retired_route(test_container: AsyncContainer) -> None:
    """An old private decision must neither consume the request nor approve retired routing."""
    chats = await test_container.get(ChatStore)
    group = await chats.get_or_create_chat(OLD)
    users = await test_container.get(UserStore)
    requester = await users.ingest_user(ACTOR)
    assert requester is not None
    administrator = await provision_role(test_container)
    grants = await test_container.get(GroupGrantStore)
    request_id = await grants.request(group.id, requester.id, ActorContext.telegram(ACTOR))
    stale = PermissionTarget("group", group.id, group.generation, OLD)
    await (await test_container.get(GroupMigrationStore)).migrate(OLD, NEW)
    service = await test_container.get(GroupActivationService)
    result = await service.decide(administrator, stale, request_id, ActivationAction.APPROVE, "private")
    assert result.outcome == ActivationOutcome.UNAVAILABLE
    assert not await grants.approvals(group.id)
    assert (await grants.requests(group.id))[0].status == RequestStatus.PENDING
    canonical = PermissionTarget("group", group.id, group.generation, NEW)
    result = await service.decide(administrator, canonical, request_id, ActivationAction.APPROVE, "private")
    assert result.outcome == ActivationOutcome.APPROVED
