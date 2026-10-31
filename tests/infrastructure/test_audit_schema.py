"""Verify entity/action audit schemas with real SQLite mutation rollback."""

from functools import partial

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.operations.ports import SystemStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.audit_schema import validate_audit_changes, validate_audit_reason
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, CommandName, Platform, Role
from hovorunv2.domain.group_grant import RequestStatus
from tests.utils.identities import create_chat_op


async def create_flushed_chat(session: AsyncSession, chat_id: int) -> ChatDB:
    """Flush a real mutation before the audit boundary validates result-derived fields."""
    chat = await create_chat_op(session, chat_id)
    await session.flush()
    return chat


@pytest.mark.parametrize(
    ("target", "action", "changes"),
    [
        ("chat", "create", {"unknown": "synthetic-private-content"}),
        ("chat", "create", {"encryption_salt": "synthetic-secret"}),
        ("chat", "create", {"platform": {"message": "synthetic-private-content"}}),
        ("chat", "create", {"platform": ["telegram", {"message": "synthetic-private-content"}]}),
        ("chat", "update_status", {"status": "synthetic-private-content"}),
        ("chat", "update_translation_settings", {"ignored_langs": [["synthetic-private-content"]]}),
        ("user_role", "set_role", {"role": [1, {"message": "synthetic-private-content"}]}),
        ("user_role", "set_role", {"role": [1, 100, 10]}),
        ("user_role", "set_role", {"role": [1, 999]}),
        ("user", "create", {"telegram_id": True}),
        ("user", "create", {"telegram_id": "88050"}),
        ("user_role", "set_role", {"role": [True, Role.OWNER]}),
        ("user_role", "set_role", {"role": [None, "100"]}),
        ("user_role", "set_role", {"role": (None, Role.OWNER)}),
        ("user_role", "set_role", {"role": [Role.USER]}),
        ("user", "set_trust", {"is_trusted": [0, 1]}),
        ("user", "set_trust", {"is_trusted": None}),
        ("chat", "update_status", {"status": None}),
        ("chat", "update_translation_settings", {"ignored_langs": ("uk", "en")}),
        ("chat", "observe_identity", {"title": ["before", "after", "third"]}),
        ("chat", "observe_identity", {"title": ["before", 42]}),
        ("command_selection", "initialize_defaults", {"commands": ["unknown"]}),
        ("unknown", "create", None),
        ("chat", "unknown", None),
    ],
)
async def test_unsupported_audit_data_rolls_back_mutation(
    test_container: AsyncContainer, target: str, action: str, changes: dict[str, object] | None
) -> None:
    """Reject both literal and result-derived fields without committing rows or audit events."""
    unit = await test_container.get(AuditedUnitOfWork)
    sessions = await test_container.get(async_sessionmaker)
    with pytest.raises(ValueError, match="Unsupported audit") as error:
        await unit.execute(
            actor=ActorContext.cli(),
            target_type=target,
            target_id=88050,
            action=action,
            operation=partial(create_flushed_chat, chat_id=88050),
            changed_fields=changes,
        )
    assert "synthetic-private-content" not in str(error.value)
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    async with sessions() as session:
        assert await session.scalar(select(ChatDB).where(ChatDB.chat_id == 88050)) is None
        assert await session.scalar(select(AuditEventDB).where(AuditEventDB.target_id == "88050")) is None


def invalid_result_changes(_result: ChatDB) -> dict[str, object]:
    """Return an invalid field after the mutation has been flushed."""
    return {"status": {"content": "synthetic-private-content"}}


async def test_result_derived_invalid_audit_rolls_back_flushed_mutation(test_container: AsyncContainer) -> None:
    """Validate callbacks inside the transaction, after operation execution and before commit."""
    unit = await test_container.get(AuditedUnitOfWork)
    sessions = await test_container.get(async_sessionmaker)
    with pytest.raises(ValueError, match="Unsupported audit"):
        await unit.execute(
            actor=ActorContext.cli(),
            target_type="chat",
            target_id=88051,
            action="update_status",
            operation=partial(create_flushed_chat, chat_id=88051),
            changed_fields=invalid_result_changes,
        )
    async with sessions() as session:
        assert await session.scalar(select(ChatDB).where(ChatDB.chat_id == 88051)) is None
        assert await session.scalar(select(AuditEventDB).where(AuditEventDB.target_id == "88051")) is None


@pytest.mark.parametrize("reason", [None, "Operator reviewed access", "Internal investigation reference 022f"])
async def test_explicit_operational_reason_is_preserved(test_container: AsyncContainer, reason: str | None) -> None:
    """Keep deliberately supplied reasons separate from changed-field payloads."""
    unit = await test_container.get(AuditedUnitOfWork)
    sessions = await test_container.get(async_sessionmaker)
    await unit.execute(
        actor=ActorContext.cli(reason=reason),
        target_type="chat",
        target_id=88052,
        action="create",
        operation=partial(create_chat_op, chat_id=88052),
        changed_fields={"platform": "telegram"},
    )
    async with sessions() as session:
        event = await session.scalar(select(AuditEventDB).where(AuditEventDB.target_id == "88052"))
        assert event is not None
        assert event.reason == reason
        assert event.changed_fields == {"platform": "telegram"}


async def test_arbitrary_system_setting_value_uses_change_marker(test_container: AsyncContainer) -> None:
    """Preserve generic settings persistence without admitting arbitrary audit payload fields."""
    settings = await test_container.get(SystemStore)
    sessions = await test_container.get(async_sessionmaker)
    await settings.set_value("custom_setting", "synthetic-private-content")
    assert await settings.get_value("custom_setting") == "synthetic-private-content"
    async with sessions() as session:
        event = await session.scalar(select(AuditEventDB).where(AuditEventDB.target_id == "custom_setting"))
        assert event is not None
        assert event.changed_fields == {"value_changed": True}


def test_approved_flat_before_after_values() -> None:
    """Accept current role/trust/settings shapes while forbidding arbitrary nested containers."""
    validate_audit_changes("user_role", "set_role", {"role": [None, 100], "user_id": 1})
    validate_audit_changes("user", "set_trust", {"is_trusted": [False, True], "trust_revision": [0, 1]})
    validate_audit_changes("chat", "update_translation_settings", {"target_lang": "uk", "ignored_langs": ["uk", "en"]})


@pytest.mark.parametrize(
    ("target", "action", "changes"),
    [
        ("chat", "create", {"platform": Platform.TELEGRAM}),
        ("chat", "update_status", {"status": ChatStatus.APPROVED, "old_status": None}),
        ("user_role", "set_role", {"role": [Role.USER, Role.OWNER]}),
        ("user_role", "set_role", {"role": [None, None]}),
        ("chat", "observe_identity", {"title": None, "chat_type": [None, "group"]}),
        ("chat", "observe_identity", {"title": "Synthetic public group"}),
        ("public_handle", "sync", {"handle": [None, "synthetic_handle"]}),
        ("command_selection", "initialize_defaults", {"commands": [CommandName.TWITTER_VIDEO]}),
        ("command_selection", "initialize_defaults", {"commands": []}),
        ("group_grant", "decide_request", {"status": [RequestStatus.PENDING, RequestStatus.APPROVED]}),
        ("group_activation", "enable", {"request_status": [None, RequestStatus.APPROVED]}),
        ("chat", "create", {}),
        ("chat", "create", None),
    ],
)
def test_standard_validation_preserves_accepted_payloads(
    target: str, action: str, changes: dict[str, object] | None
) -> None:
    """Preserve enum objects, wire values, nullable fields and intentionally partial payloads."""
    validate_audit_changes(target, action, changes)


async def test_unknown_event_rejected_even_when_no_audit_would_be_written(test_container: AsyncContainer) -> None:
    """Keep event allowlisting ahead of operation execution and no-op suppression."""
    unit = await test_container.get(AuditedUnitOfWork)
    sessions = await test_container.get(async_sessionmaker)
    with pytest.raises(ValueError, match="Unsupported audit entity/action"):
        await unit.execute(
            actor=ActorContext.cli(),
            target_type="chat",
            target_id=88053,
            action="unknown",
            operation=partial(create_flushed_chat, chat_id=88053),
            audit_if=lambda _result: False,
        )
    async with sessions() as session:
        assert await session.scalar(select(ChatDB).where(ChatDB.chat_id == 88053)) is None


@pytest.mark.parametrize("reason", [{"message": "synthetic-private-content"}, ["synthetic-private-content"], 42])
def test_reason_rejects_structured_payloads(reason: object) -> None:
    """Reject structured values instead of converting arbitrary payloads into reason text."""
    with pytest.raises(TypeError, match="explicit operational text"):
        validate_audit_reason(reason)
