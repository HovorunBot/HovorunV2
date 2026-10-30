"""Explicit feature selection, legacy compatibility and atomic audit behavior."""

from datetime import UTC, datetime

import pytest
from dishka import AsyncContainer
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hovorunv2.application.activation import ActivationOutcome, ActivationRequest, ActivationStore
from hovorunv2.application.group_migration import GroupMigrationStore
from hovorunv2.application.ports import ChatStore
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.domain.audit import AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.authorization.casbin_policy import load_authorization_policy
from hovorunv2.infrastructure.fixtures import setup_fixtures
from hovorunv2.infrastructure.persistence.audited_transaction import SQLAlchemyAuditedTransaction
from hovorunv2.infrastructure.persistence.command_store import SQLAlchemyCommandStore
from tests.utils.activation import ACTOR, GROUP, seed_activation
from tests.utils.postgres import reject_audit


@pytest.mark.parametrize("chat_id", [-29001, 29001])
async def test_explicit_empty_stays_disabled_after_defaults_and_fixtures(
    test_engine: AsyncEngine, chat_id: int
) -> None:
    """Private and group records preserve deliberate empty settings through retries."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    await setup_fixtures(sessions)
    service = CommandService(
        SQLAlchemyCommandStore(sessions, SQLAlchemyAuditedTransaction(sessions), await load_authorization_policy())
    )
    assert not await service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=True)
    await service.enable_command(chat_id, CommandName.TWITTER)
    await service.disable_command(chat_id, CommandName.TWITTER)
    await service.enable_defaults(chat_id)
    await setup_fixtures(sessions)
    assert await service.get_allowed_commands(chat_id) == set()
    assert not await service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=True)
    assert not await service.is_capture_allowed(chat_id, CommandName.TWITTER, is_video=False)
    async with sessions() as session:
        assert (
            len(
                (
                    await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "command_association"))
                ).all()
            )
            == 4
        )


async def test_legacy_compatibility_ends_on_intentional_edit(test_engine: AsyncEngine) -> None:
    """Known migrated empty capture remains until an actual selection edit."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    await setup_fixtures(sessions)
    async with sessions.begin() as session:
        session.add(ChatDB(chat_id=-29002, legacy_empty_capture=True))
    service = CommandService(
        SQLAlchemyCommandStore(sessions, SQLAlchemyAuditedTransaction(sessions), await load_authorization_policy())
    )
    assert await service.is_capture_allowed(-29002, CommandName.TWITTER, is_video=True)
    await service.enable_defaults(-29002)
    assert await service.get_allowed_commands(-29002) == set()
    assert await service.disable_command(-29002, CommandName.TWITTER_POST)
    assert not await service.is_capture_allowed(-29002, CommandName.TWITTER, is_video=True)
    await service.enable_defaults(-29002)
    assert await service.get_allowed_commands(-29002) == set()
    async with sessions() as session:
        event = (
            await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "command_association"))
        ).one()
        assert event.changed_fields["legacy_empty_capture"] is False


async def test_selection_audit_failure_rolls_back_legacy_transition(test_engine: AsyncEngine) -> None:
    """An audit insertion failure preserves both selections and legacy compatibility."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    await setup_fixtures(sessions)
    async with sessions.begin() as session:
        session.add(ChatDB(chat_id=-29003, legacy_empty_capture=True))
        await reject_audit(
            session, "reject_selection_audit", "NEW.target_type = 'command_association'", "audit rejected"
        )
    service = CommandService(
        SQLAlchemyCommandStore(sessions, SQLAlchemyAuditedTransaction(sessions), await load_authorization_policy())
    )
    with pytest.raises(IntegrityError, match="audit rejected"):
        await service.enable_command(-29003, CommandName.TWITTER_POST)
    assert await service.get_allowed_commands(-29003) == set()
    assert await service.is_capture_allowed(-29003, CommandName.TWITTER, is_video=True)
    async with sessions() as session:
        chat = (await session.scalars(select(ChatDB).where(ChatDB.chat_id == -29003))).one()
        assert not chat.features_configured


async def test_deleted_selection_never_resurrects_target(test_engine: AsyncEngine) -> None:
    """Old settings routes cannot recreate a soft-deleted identity."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    await setup_fixtures(sessions)
    async with sessions.begin() as session:
        session.add(ChatDB(chat_id=-29004, legacy_empty_capture=True, deleted_at=datetime.now(UTC)))
    service = CommandService(
        SQLAlchemyCommandStore(sessions, SQLAlchemyAuditedTransaction(sessions), await load_authorization_policy())
    )
    assert not await service.enable_command(-29004, CommandName.TWITTER_POST)
    assert not await service.is_capture_allowed(-29004, CommandName.TWITTER, is_video=True)
    async with sessions() as session:
        assert len((await session.scalars(select(ChatDB).where(ChatDB.chat_id == -29004))).all()) == 1


async def test_first_explicit_off_blocks_later_default_initialization(test_engine: AsyncEngine) -> None:
    """Selecting off on a new empty record is still a durable settings decision."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    await setup_fixtures(sessions)
    async with sessions.begin() as session:
        session.add(ChatDB(chat_id=-29005))
    service = CommandService(
        SQLAlchemyCommandStore(sessions, SQLAlchemyAuditedTransaction(sessions), await load_authorization_policy())
    )
    assert not await service.disable_command(-29005, CommandName.TWITTER_POST)
    await service.enable_defaults(-29005)
    assert await service.get_allowed_commands(-29005) == set()
    assert not await service.is_capture_allowed(-29005, CommandName.TWITTER, is_video=False)
    async with sessions() as session:
        events = (
            await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "command_association"))
        ).all()
        assert len(events) == 1
        assert events[0].action == "disable_command"
    assert not await service.disable_command(-29005, CommandName.TWITTER_POST)
    async with sessions() as session:
        assert (
            len(
                (
                    await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "command_association"))
                ).all()
            )
            == 1
        )


async def test_migration_keeps_explicit_off_history(test_container: AsyncContainer) -> None:
    """Historical route IDs still bind explicit settings after canonical routing changes."""
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(-29006)
    commands = await test_container.get(CommandService)
    await commands.disable_command(-29006, CommandName.TWITTER_POST)
    migrations = await test_container.get(GroupMigrationStore)
    assert await migrations.migrate(-29006, -10029006)
    await commands.enable_defaults(-10029006)
    assert await commands.get_allowed_commands(-10029006) == set()
    assert not await commands.is_capture_allowed(-10029006, CommandName.TWITTER, is_video=False)


@pytest.mark.parametrize("migrate", [False, True])
async def test_selection_intent_survives_missing_audit_history(
    test_container: AsyncContainer, *, migrate: bool
) -> None:
    """Business configuration does not depend on retained audit rows or routing IDs."""
    chats = await test_container.get(ChatStore)
    chat = await chats.get_or_create_chat(-29007)
    commands = await test_container.get(CommandService)
    await commands.disable_command(-29007, CommandName.TWITTER_POST)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        await session.execute(delete(AuditEventDB))
    target = -29007
    if migrate:
        migrations = await test_container.get(GroupMigrationStore)
        assert await migrations.migrate(target, -10029007)
        target = -10029007
    await commands.enable_defaults(target)
    assert await commands.get_allowed_commands(target) == set()
    assert not await commands.is_capture_allowed(target, CommandName.TWITTER, is_video=True)
    async with sessions() as session:
        saved = await session.get(ChatDB, chat.id)
        assert saved is not None
        assert saved.features_configured


async def test_defaults_initialize_once_without_audit_history(test_engine: AsyncEngine) -> None:
    """Successful first configuration remains explicit after its audit rows are removed."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    await setup_fixtures(sessions)
    async with sessions.begin() as session:
        session.add(ChatDB(chat_id=-29008))
    store = SQLAlchemyCommandStore(sessions, SQLAlchemyAuditedTransaction(sessions), await load_authorization_policy())
    await store.initialize_defaults(-29008, ())
    async with sessions.begin() as session:
        await session.execute(delete(AuditEventDB))
    await store.initialize_defaults(-29008, (CommandName.TWITTER_POST,))
    assert await store.get_allowed_commands(-29008) == []


async def test_first_activation_preserves_empty_selection_without_audit_history(
    test_container: AsyncContainer,
) -> None:
    """Activation reads chat configuration intent rather than reconstructing audit history."""
    target = await seed_activation(test_container)
    commands = await test_container.get(CommandService)
    await commands.disable_command(GROUP, CommandName.TWITTER_POST)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        await session.execute(delete(AuditEventDB))
    activation = await test_container.get(ActivationStore)
    result = await activation.execute(ActivationRequest(ACTOR, target, "group"))
    assert result.outcome == ActivationOutcome.APPROVED
    assert await commands.get_allowed_commands(GROUP) == set()


async def test_default_audit_failure_rolls_back_configuration_intent(test_engine: AsyncEngine) -> None:
    """A failed first-default audit leaves a new chat untouched and retryable."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    await setup_fixtures(sessions)
    async with sessions.begin() as session:
        session.add(ChatDB(chat_id=-29009))
        await reject_audit(session, "reject_default_audit", "NEW.target_type = 'command_selection'", "audit rejected")
    store = SQLAlchemyCommandStore(sessions, SQLAlchemyAuditedTransaction(sessions), await load_authorization_policy())
    with pytest.raises(IntegrityError, match="audit rejected"):
        await store.initialize_defaults(-29009, (CommandName.TWITTER_POST,))
    assert await store.get_allowed_commands(-29009) == []
    async with sessions() as session:
        chat = (await session.scalars(select(ChatDB).where(ChatDB.chat_id == -29009))).one()
        assert not chat.features_configured
