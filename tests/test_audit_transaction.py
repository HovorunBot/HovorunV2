"""Tests for audited transaction boundary, audit events, and sanitization."""

from copy import deepcopy
from functools import partial

import pytest
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.ports import AuditedUnitOfWork
from hovorunv2.domain.audit import (
    SENSITIVE_FIELD_MARKER,
    ActorContext,
    sanitize_audit_changes,
)
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ActorOrigin, ChatStatus
from hovorunv2.infrastructure.persistence.audited_transaction import SQLAlchemyAuditedTransaction
from hovorunv2.infrastructure.repositories.audit_repository import SQLAlchemyAuditRepository


def test_sanitize_audit_changes_redacts_secrets_and_payloads() -> None:
    """Verify sensitive fields and payload texts are redacted while safe fields are kept."""
    raw_changes = {
        "status": "approved",
        "encryption_salt": "0123456789abcdef",
        "cache_encryption_key": "super_secret_key",
        "api_token": "secret_token_123",
        "nested_dict": {
            "title": "My Group",
            "salt": "sensitive_salt",
            "message_text": "hello private world",
        },
        "allowed_field": 42,
    }

    sanitized = sanitize_audit_changes(raw_changes)
    assert sanitized is not None
    assert sanitized["status"] == "approved"
    assert sanitized["allowed_field"] == 42
    assert sanitized["encryption_salt"] == SENSITIVE_FIELD_MARKER
    assert sanitized["cache_encryption_key"] == SENSITIVE_FIELD_MARKER
    assert sanitized["api_token"] == SENSITIVE_FIELD_MARKER
    assert sanitized["nested_dict"]["title"] == "My Group"
    assert sanitized["nested_dict"]["salt"] == SENSITIVE_FIELD_MARKER
    assert sanitized["nested_dict"]["message_text"] == SENSITIVE_FIELD_MARKER


def test_actor_contexts_support_cli_system_and_telegram() -> None:
    """Verify actor contexts accurately reflect origin without fabricated user IDs."""
    sys_actor = ActorContext.system(reason="hourly cron maintenance")
    assert sys_actor.origin == ActorOrigin.SYSTEM
    assert sys_actor.actor_id is None
    assert sys_actor.reason == "hourly cron maintenance"

    cli_actor = ActorContext.cli(actor_id=None, reason="manual operator bootstrap")
    assert cli_actor.origin == ActorOrigin.CLI
    assert cli_actor.actor_id is None
    assert cli_actor.reason == "manual operator bootstrap"

    tg_actor = ActorContext.telegram(user_id=123456789, reason="user /settings toggle", correlation_id="req-abc")
    assert tg_actor.origin == ActorOrigin.TELEGRAM
    assert tg_actor.actor_id == 123456789
    assert tg_actor.correlation_id == "req-abc"


def test_sanitize_nested_containers_and_mixed_case_without_mutating_input() -> None:
    """Redact at every depth while preserving benign falsey values and input data."""
    raw_changes = {
        "BODY": "synthetic private content",
        "settings": {
            "api_key": "synthetic secret",
            "deeper": {"Secret": "synthetic secret", "enabled": False},
            "items": [{"TOKEN": "synthetic secret", "count": 0}, "", [], {}],
            "previous_next": ({"message_text": "synthetic private content"}, None),
        },
    }
    original = deepcopy(raw_changes)

    assert sanitize_audit_changes(raw_changes) == {
        "BODY": SENSITIVE_FIELD_MARKER,
        "settings": {
            "api_key": SENSITIVE_FIELD_MARKER,
            "deeper": {"Secret": SENSITIVE_FIELD_MARKER, "enabled": False},
            "items": [{"TOKEN": SENSITIVE_FIELD_MARKER, "count": 0}, "", [], {}],
            "previous_next": ({"message_text": SENSITIVE_FIELD_MARKER}, None),
        },
    }
    assert raw_changes == original


@pytest.mark.parametrize("changes", [None, {}])
def test_sanitize_empty_changes(changes: dict[str, object] | None) -> None:
    """Preserve the absence of audit changes without fabricating values."""
    assert sanitize_audit_changes(changes) == changes


async def create_chat_op(session: AsyncSession, chat_id: int) -> ChatDB:
    """Add a chat entity in session."""
    chat = ChatDB(chat_id=chat_id, status=ChatStatus.APPROVED)
    session.add(chat)
    return chat


async def failing_op(session: AsyncSession, chat_id: int) -> None:
    """Operation that intentionally raises error after adding entity."""
    chat = ChatDB(chat_id=chat_id, status=ChatStatus.APPROVED)
    session.add(chat)
    await session.flush()
    msg = "Synthetic failure inside transaction"
    raise RuntimeError(msg)


async def noop_op(_session: AsyncSession) -> None:
    """No-op test operation."""


@pytest.mark.asyncio
async def test_audited_transaction_commits_mutation_and_audit_together(test_container: AsyncContainer) -> None:
    """Verify a business mutation and audit record commit together in a single SQLite transaction."""
    unit_of_work = await test_container.get(AuditedUnitOfWork)
    session_maker = await test_container.get(async_sessionmaker)

    chat_id = 88001
    actor = ActorContext.telegram(user_id=1001, reason="approve group")

    result_chat = await unit_of_work.execute(
        actor=actor,
        target_type="chat",
        target_id=chat_id,
        action="approve",
        operation=partial(create_chat_op, chat_id=chat_id),
        changed_fields={
            "status": "approved",
            "encryption_salt": "012345",
            "details": [{"api_key": "synthetic secret", "count": 0}],
        },
    )
    assert result_chat.chat_id == chat_id

    # Direct SQLite query verifying both the mutation and the audit event exist
    async with session_maker() as session:
        # Check chat was persisted
        chat_stmt = select(ChatDB).where(ChatDB.chat_id == chat_id)
        chat = (await session.execute(chat_stmt)).scalar_one_or_none()
        assert chat is not None
        assert chat.status == ChatStatus.APPROVED

        # Check audit event was persisted in same transaction
        audit_repo = SQLAlchemyAuditRepository(session)
        events = await audit_repo.get_by_target("chat", str(chat_id))
        assert len(events) == 1
        event = events[0]
        assert event.actor_origin == ActorOrigin.TELEGRAM
        assert event.actor_id == 1001
        assert event.action == "approve"
        assert event.reason == "approve group"
        assert event.changed_fields is not None
        assert event.changed_fields["status"] == "approved"
        # Salt must be redacted in persisted DB record
        assert event.changed_fields["encryption_salt"] == SENSITIVE_FIELD_MARKER
        assert event.changed_fields["details"] == [{"api_key": SENSITIVE_FIELD_MARKER, "count": 0}]


@pytest.mark.asyncio
async def test_audited_transaction_rolls_back_both_on_failure(test_container: AsyncContainer) -> None:
    """Verify business mutation and audit record roll back together if an operation fails."""
    unit_of_work = await test_container.get(AuditedUnitOfWork)
    session_maker = await test_container.get(async_sessionmaker)

    chat_id = 88002
    actor = ActorContext.cli(reason="failed batch import")

    with pytest.raises(RuntimeError, match="Synthetic failure inside transaction"):
        await unit_of_work.execute(
            actor=actor,
            target_type="chat",
            target_id=chat_id,
            action="create",
            operation=partial(failing_op, chat_id=chat_id),
            changed_fields={"status": "approved"},
        )

    # Direct SQLite check: NEITHER the chat NOR the audit event exists
    async with session_maker() as session:
        chat_stmt = select(ChatDB).where(ChatDB.chat_id == chat_id)
        chat = (await session.execute(chat_stmt)).scalar_one_or_none()
        assert chat is None, "Mutation should have rolled back"

        audit_repo = SQLAlchemyAuditRepository(session)
        events = await audit_repo.get_by_target("chat", str(chat_id))
        assert len(events) == 0, "Audit event should have rolled back"


@pytest.mark.asyncio
async def test_audited_transaction_forbids_recursive_audit(test_container: AsyncContainer) -> None:
    """Verify auditing of audit entities is explicitly rejected."""
    unit_of_work = await test_container.get(AuditedUnitOfWork)

    actor = ActorContext.system()

    async def noop_op(_session: AsyncSession) -> None:
        pass

    with pytest.raises(ValueError, match="Recursive audit logging of audit entities is forbidden"):
        await unit_of_work.execute(
            actor=actor,
            target_type="audit",
            target_id="1",
            action="create",
            operation=noop_op,
        )


@pytest.mark.asyncio
async def test_dishka_provides_audited_unit_of_work(test_container: AsyncContainer) -> None:
    """Verify Dishka composition root provides AuditedUnitOfWork backed by real SQLite."""
    uow = await test_container.get(AuditedUnitOfWork)
    assert isinstance(uow, SQLAlchemyAuditedTransaction)
