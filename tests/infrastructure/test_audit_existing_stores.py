"""Integration tests verifying audited transaction coverage for existing persistence stores."""

from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.operations.ports import SystemStore
from hovorunv2.domain.audit import AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import ChatStatus, CommandName
from hovorunv2.domain.system import SystemStateDB
from hovorunv2.infrastructure.fixtures import setup_fixtures


async def test_command_association_audit(test_container: AsyncContainer) -> None:
    """A command association add/remove has exactly one audit action with the same outcome."""
    session_maker = await test_container.get(async_sessionmaker)
    command_store = await test_container.get(CommandStore)

    chat_id = 998877
    cmd_name = CommandName.HELP.value

    # First enable creates association and 1 audit event
    enabled = await command_store.enable_command(chat_id, cmd_name)
    assert enabled is True

    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type == "command_association",
                        AuditEventDB.target_id == f"{chat_id}:{cmd_name}",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].action == "enable_command"
        assert events[0].changed_fields == {"chat_id": chat_id, "platform": "telegram", "command": cmd_name}

    # Re-enabling an already enabled command is a no-op and does not emit a spurious audit event
    enabled_again = await command_store.enable_command(chat_id, cmd_name)
    assert enabled_again is True

    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type == "command_association",
                        AuditEventDB.target_id == f"{chat_id}:{cmd_name}",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1

    # Disabling the command emits exactly 1 disable audit event
    disabled = await command_store.disable_command(chat_id, cmd_name)
    assert disabled is True

    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB)
                    .where(
                        AuditEventDB.target_type == "command_association",
                        AuditEventDB.target_id == f"{chat_id}:{cmd_name}",
                    )
                    .order_by(AuditEventDB.id.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 2
        assert events[1].action == "disable_command"

    # Disabling again returns False and produces no audit event
    disabled_again = await command_store.disable_command(chat_id, cmd_name)
    assert disabled_again is False

    async with session_maker() as session:
        count = (
            (
                await session.execute(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type == "command_association",
                        AuditEventDB.target_id == f"{chat_id}:{cmd_name}",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 2


async def test_salt_and_secrets_redacted_in_audit(test_container: AsyncContainer) -> None:
    """Salt and material secrets are absent from audit serialization; safe fields remain."""
    session_maker = await test_container.get(async_sessionmaker)
    chat_store = await test_container.get(ChatStore)
    system_store = await test_container.get(SystemStore)

    chat_id = 112233
    raw_salt = "super_secret_salt_material_445566"

    # 1. Salt is updated in chat store
    await chat_store.set_encryption_salt(chat_id, raw_salt)

    async with session_maker() as session:
        chat = (await session.execute(select(ChatDB).where(ChatDB.chat_id == chat_id))).scalar_one()
        assert chat.encryption_salt == raw_salt

        audit_event = (
            await session.execute(
                select(AuditEventDB).where(
                    AuditEventDB.target_type == "chat",
                    AuditEventDB.target_id == str(chat_id),
                    AuditEventDB.action == "set_encryption_salt",
                )
            )
        ).scalar_one()

        # Salt is redacted in audit log
        assert audit_event.changed_fields is not None
        assert audit_event.changed_fields["encryption_salt"] == "[REDACTED_CHANGED]"
        assert raw_salt not in str(audit_event.changed_fields)

    # 2. Secret in system store is redacted
    await system_store.set_value("bot_api_token", "secret_value_xyz")
    await system_store.set_value("last_notified_version", "0.3.0")

    async with session_maker() as session:
        stmt = select(SystemStateDB).where(SystemStateDB.key == "bot_api_token")
        token_val = (await session.execute(stmt)).scalar_one()
        assert token_val.value == "secret_value_xyz"

        token_audit = (
            await session.execute(
                select(AuditEventDB).where(
                    AuditEventDB.target_type == "system_setting",
                    AuditEventDB.target_id == "bot_api_token",
                )
            )
        ).scalar_one()
        assert token_audit.changed_fields is not None
        assert token_audit.changed_fields == {"value_changed": True}
        assert "secret_value_xyz" not in str(token_audit.changed_fields)

        version_audit = (
            await session.execute(
                select(AuditEventDB).where(
                    AuditEventDB.target_type == "system_setting",
                    AuditEventDB.target_id == "last_notified_version",
                )
            )
        ).scalar_one()
        assert version_audit.changed_fields == {"last_notified_version": "0.3.0"}


async def test_repeated_startup_fixtures_idempotent(test_container: AsyncContainer) -> None:
    """Repeated startup fixtures do not duplicate records or produce spurious mutation events."""
    session_maker = await test_container.get(async_sessionmaker)

    # Count current command_fixture audit events and commands
    async with session_maker() as session:
        initial_events = (
            (await session.execute(select(AuditEventDB).where(AuditEventDB.target_type == "command_fixture")))
            .scalars()
            .all()
        )
        initial_event_count = len(initial_events)
        initial_commands_count = len((await session.execute(select(CommandDB))).scalars().all())

    # Running setup_fixtures again should be a complete no-op
    await setup_fixtures(session_maker)

    async with session_maker() as session:
        after_events = (
            (await session.execute(select(AuditEventDB).where(AuditEventDB.target_type == "command_fixture")))
            .scalars()
            .all()
        )
        after_commands_count = len((await session.execute(select(CommandDB))).scalars().all())

        assert len(after_events) == initial_event_count
        assert after_commands_count == initial_commands_count


async def test_chat_status_and_translation_audit(test_container: AsyncContainer) -> None:
    """Chat creation, status update, and translation settings are audited."""
    session_maker = await test_container.get(async_sessionmaker)
    chat_store = await test_container.get(ChatStore)

    chat_id = 554433

    # get_or_create creates new chat and audits creation
    created_chat = await chat_store.get_or_create_chat(chat_id)
    assert created_chat.chat_id == chat_id

    async with session_maker() as session:
        create_audit = (
            await session.execute(
                select(AuditEventDB).where(
                    AuditEventDB.target_type == "chat",
                    AuditEventDB.target_id == str(chat_id),
                    AuditEventDB.action == "create",
                )
            )
        ).scalar_one()
        assert create_audit.changed_fields == {"platform": "telegram"}

    # Repeated get_or_create returns existing chat without extra audit event
    _ = await chat_store.get_or_create_chat(chat_id)
    async with session_maker() as session:
        chat_audits = (
            (
                await session.execute(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type == "chat",
                        AuditEventDB.target_id == str(chat_id),
                        AuditEventDB.action == "create",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(chat_audits) == 1

    # Status update is audited
    await chat_store.update_status(chat_id, ChatStatus.APPROVED)
    async with session_maker() as session:
        status_audit = (
            await session.execute(
                select(AuditEventDB).where(
                    AuditEventDB.target_type == "chat",
                    AuditEventDB.target_id == str(chat_id),
                    AuditEventDB.action == "update_status",
                )
            )
        ).scalar_one()
        assert status_audit.changed_fields == {"status": "approved", "old_status": "unauthorized"}

    # Translation settings update is audited
    await chat_store.update_translation_settings(chat_id, "uk", ["en", "de"])
    async with session_maker() as session:
        lang_audit = (
            await session.execute(
                select(AuditEventDB).where(
                    AuditEventDB.target_type == "chat",
                    AuditEventDB.target_id == str(chat_id),
                    AuditEventDB.action == "update_translation_settings",
                )
            )
        ).scalar_one()
        assert lang_audit.changed_fields == {"target_lang": "uk", "ignored_langs": ["en", "de"]}
