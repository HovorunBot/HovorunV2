"""Immutable notification events and recipient outcomes against real PostgreSQL."""

from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.operations.notification_ledger import (
    NotificationEvent,
    NotificationLedgerService,
    NotificationOutcome,
)
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.notification_event import NotificationEventDB, NotificationReceiptDB
from tests.utils.usage import usage_subjects

EVENT = NotificationEvent("release:v0.3.0", NotificationCategory.RELEASE, "release", "v0.3.0")


async def _service(container: AsyncContainer) -> NotificationLedgerService:
    await usage_subjects(container)
    return await container.get(NotificationLedgerService)


async def test_event_creation_is_idempotent_by_immutable_key(test_container: AsyncContainer) -> None:
    """A duplicate event key never creates another logical event or receipt."""
    service = await _service(test_container)
    actor = ActorContext.cli()
    assert await service.ensure_event(EVENT, actor=actor) is True
    assert await service.ensure_event(EVENT, actor=actor) is False
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationEventDB)) == 1


async def test_recipient_failure_does_not_reset_successful_recipient(
    test_container: AsyncContainer,
) -> None:
    """Receipts are per recipient; a retry on one never resets another's delivery."""
    service = await _service(test_container)
    user, group = await usage_subjects(test_container)
    actor = ActorContext.cli()
    await service.ensure_event(EVENT, actor=actor)
    assert await service.record(
        EVENT.event_key,
        user.target,
        NotificationOutcome.DELIVERED,
        attempts=1,
        telegram_message_id=42,
        actor=actor,
    )
    assert await service.record(
        EVENT.event_key,
        group.target,
        NotificationOutcome.RETRY,
        attempts=2,
        actor=actor,
    )
    user_receipt = await service.receipt(EVENT.event_key, user.target)
    assert user_receipt is not None
    assert user_receipt.outcome == NotificationOutcome.DELIVERED
    assert user_receipt.telegram_message_id == 42
    pending = await service.pending(EVENT.event_key)
    assert [receipt.target.kind for receipt in pending] == ["group"]
    assert await service.should_deliver(EVENT.event_key, user.target) is False
    assert await service.should_deliver(EVENT.event_key, group.target) is True
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationReceiptDB)) == 2
        audits = list(
            (
                await session.scalars(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type.in_(("notification_event", "notification_receipt"))
                    )
                )
            ).all()
        )
        assert len(audits) == 3
        assert await session.scalar(select(func.count()).select_from(NotificationEventDB)) == 1


async def test_duplicate_receipt_updates_one_row(test_container: AsyncContainer) -> None:
    """Replay of the same recipient updates its single receipt rather than duplicating it."""
    service = await _service(test_container)
    user, _group = await usage_subjects(test_container)
    actor = ActorContext.cli()
    await service.ensure_event(EVENT, actor=actor)
    await service.record(EVENT.event_key, user.target, NotificationOutcome.PENDING, attempts=0, actor=actor)
    await service.record(
        EVENT.event_key,
        user.target,
        NotificationOutcome.DELIVERED,
        attempts=1,
        telegram_message_id=7,
        actor=actor,
    )
    receipt = await service.receipt(EVENT.event_key, user.target)
    assert receipt is not None
    assert receipt.outcome == NotificationOutcome.DELIVERED
    assert receipt.attempts == 1
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationReceiptDB)) == 1
