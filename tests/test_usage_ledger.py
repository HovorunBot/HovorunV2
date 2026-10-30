"""Stable-subject usage accounting, replay safety and transaction rollback with real SQLite."""

import asyncio
from dataclasses import replace

import pytest
from dishka import AsyncContainer
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hovorunv2.application.features import FEATURE_REGISTRY, FeatureDefinition, FeatureRegistry
from hovorunv2.application.ports import AuditedUnitOfWork, ChatStore, UserStore
from hovorunv2.application.usage import MEDIA_BUCKET, UsageUnit, UsageWindow
from hovorunv2.application.usage_ledger import (
    MAX_ACCOUNTING_INTEGER,
    MAX_USAGE_BATCH,
    AccountingSubject,
    AppliedUsage,
    UsageCharge,
    UsageEvent,
    UsageLedgerStore,
)
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.base import Base
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import Platform, TargetKind
from hovorunv2.domain.usage_accounting import UsageAccountingEventDB, UsageAggregateDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.audited_transaction import SQLAlchemyAuditedTransaction
from hovorunv2.infrastructure.persistence.usage_ledger_store import SQLAlchemyUsageLedgerStore
from tests.utils.postgres import reject_audit
from tests.utils.usage import GROUP, USER, usage_subjects

FEATURE_BUCKET = f"feature:{FEATURE_REGISTRY.all()[0].key}"
SUBJECT = AccountingSubject(TargetKind.USER, USER)
ACTOR = ActorContext.system(reason="usage_accounting")


def usage_event(
    event_id: str, *, amount: int = 1, timestamp: int = 90, subject: AccountingSubject = SUBJECT
) -> UsageEvent:
    """Use deterministic UTC windows and technical IDs, never request payloads."""
    return UsageEvent(
        event_id, subject, timestamp, (UsageCharge(FEATURE_BUCKET, amount), UsageCharge(MEDIA_BUCKET, amount))
    )


async def test_replayed_events_and_reordered_charges_change_totals_once(test_container: AsyncContainer) -> None:
    """Duplicate deliveries, including duplicates inside one batch, neither charge nor audit twice."""
    await usage_subjects(test_container)
    store = await test_container.get(UsageLedgerStore)
    event = usage_event("job-1:completion")
    assert await store.apply_batch((event, event), actor=ACTOR) == AppliedUsage(1, 2, 2)
    assert (
        await store.apply_batch((replace(event, charges=tuple(reversed(event.charges))),), actor=ACTOR)
        == AppliedUsage()
    )
    assert await store.total(SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 1
    assert await store.total(SUBJECT, FEATURE_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 1
    assert await store.total(SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 120) == 0
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(UsageAccountingEventDB)) == 1
        events = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "usage_batch"))).all()
        )
        assert len(events) == 1
        assert events[0].changed_fields == {"events_applied": 1, "aggregates_updated": 2, "units_added": 2}
        assert events[0].actor_origin == "system"


async def test_conflicting_replay_rolls_back_earlier_work_in_batch(test_container: AsyncContainer) -> None:
    """An event key cannot silently change amount, subject, window or charge dimensions."""
    await usage_subjects(test_container)
    store = await test_container.get(UsageLedgerStore)
    original = usage_event("stable-event")
    await store.apply_batch((original,), actor=ACTOR)
    for conflict in (
        usage_event("stable-event", amount=2),
        usage_event("stable-event", timestamp=150),
        usage_event("stable-event", subject=AccountingSubject(TargetKind.GROUP, GROUP)),
        replace(original, charges=(original.charges[0],)),
    ):
        with pytest.raises(ValueError, match="different charges"):
            await store.apply_batch((usage_event("must-roll-back"), conflict), actor=ACTOR)
        assert await store.total(SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 1
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        assert await session.get(UsageAccountingEventDB, "must-roll-back") is None
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEventDB).where(AuditEventDB.target_type == "usage_batch")
            )
            == 1
        )


async def test_batch_sums_multiple_events_into_one_audited_aggregate_update(test_container: AsyncContainer) -> None:
    """Successful consumption aggregates by unit/window; one bounded batch creates one audit summary."""
    await usage_subjects(test_container)
    store = await test_container.get(UsageLedgerStore)
    assert await store.apply_batch(
        (usage_event("job-a", amount=3), usage_event("job-b", amount=4)), actor=ACTOR
    ) == AppliedUsage(2, 2, 14)
    assert await store.total(SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 7
    assert await store.apply_batch((), actor=ACTOR) == AppliedUsage()
    group = AccountingSubject(TargetKind.GROUP, GROUP)
    await store.apply_batch((usage_event("group-job", amount=2, subject=group),), actor=ACTOR)
    assert await store.total(group, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 2
    assert await store.total(SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 7


@pytest.mark.parametrize("kind", [TargetKind.USER, TargetKind.GROUP])
async def test_fresh_generations_and_deleted_subjects_keep_current_window_usage(
    test_container: AsyncContainer, kind: TargetKind
) -> None:
    """Accounting survives deletion and a fresh internal generation of the same numeric subject."""
    user, group = await usage_subjects(test_container)
    current = user if kind == TargetKind.USER else group
    subject = AccountingSubject(kind, current.telegram_id)
    store = await test_container.get(UsageLedgerStore)
    await store.apply_batch((usage_event("before-deletion", amount=4, subject=subject),), actor=ACTOR)
    if kind == TargetKind.USER:
        await (await test_container.get(UserStore)).soft_delete_user(USER, actor=ActorContext.cli(), reason="synthetic")
    else:
        await (await test_container.get(ChatStore)).soft_delete_chat(
            GROUP, actor=ActorContext.cli(), reason="synthetic"
        )
    assert await store.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 4
    await store.apply_batch((usage_event("delayed-accounting", subject=subject),), actor=ACTOR)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        fresh = (
            UserDB(telegram_id=USER, generation=current.generation + 1)
            if kind == TargetKind.USER
            else ChatDB(
                chat_id=GROUP,
                platform=Platform.TELEGRAM,
                chat_type="group",
                generation=current.generation + 1,
            )
        )
        session.add(fresh)
        await session.flush()
        assert fresh.id != current.entity_id
        await session.commit()
    assert await store.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 5
    await store.apply_batch((usage_event("fresh-generation-completion", subject=subject),), actor=ACTOR)
    assert await store.total(subject, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 6


@pytest.mark.fresh_schema
async def test_audit_failure_rolls_back_receipt_and_aggregate(test_container: AsyncContainer) -> None:
    """A SQLite audit rejection proves accounting and its replay receipt share the audit transaction."""
    await usage_subjects(test_container)
    store = await test_container.get(UsageLedgerStore)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        await reject_audit(session, "reject_accounting_audit", "NEW.target_type = 'usage_batch'", "synthetic")
        await session.commit()
    with pytest.raises(IntegrityError):
        await store.apply_batch((usage_event("retry-after-audit-failure"),), actor=ACTOR)
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(UsageAggregateDB)) == 0
        assert await session.scalar(select(func.count()).select_from(UsageAccountingEventDB)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEventDB).where(AuditEventDB.target_type == "usage_batch")
            )
            == 0
        )
        await session.execute(text("DROP TRIGGER reject_accounting_audit ON audit_events"))
        await session.commit()
    assert (await store.apply_batch((usage_event("retry-after-audit-failure"),), actor=ACTOR)).events_applied == 1


async def test_unknown_subject_unit_batch_and_telegram_origin_fail_closed(test_container: AsyncContainer) -> None:
    """Accounting cannot create identities, accept unsupported units, or act as a public mutation API."""
    await usage_subjects(test_container)
    store = await test_container.get(UsageLedgerStore)
    with pytest.raises(PermissionError, match="system or CLI"):
        await store.apply_batch((usage_event("forged"),), actor=ActorContext.telegram(USER))
    with pytest.raises(ValueError, match="batch exceeds"):
        await store.apply_batch((usage_event("too-many"),) * (MAX_USAGE_BATCH + 1), actor=ACTOR)
    with pytest.raises(ValueError, match="known current or historical"):
        await store.apply_batch(
            (usage_event("unknown", subject=AccountingSubject(TargetKind.USER, USER + 99)),), actor=ACTOR
        )
    with pytest.raises(ValueError, match="unsupported"):
        await store.apply_batch(
            (replace(usage_event("tokens"), charges=(UsageCharge(MEDIA_BUCKET, 1, UsageUnit.TOKENS),)),), actor=ACTOR
        )
    with pytest.raises(ValueError, match="Unknown feature"):
        await store.apply_batch(
            (replace(usage_event("unknown-feature"), charges=(UsageCharge("feature:unknown", 1),)),), actor=ACTOR
        )
    assert await store.total(SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 0


async def test_aggregate_integer_overflow_rolls_back_new_receipt(test_container: AsyncContainer) -> None:
    """Overflow cannot convert exact accounting to SQLite floating-point arithmetic."""
    await usage_subjects(test_container)
    store = await test_container.get(UsageLedgerStore)
    await store.apply_batch((usage_event("maximum", amount=MAX_ACCOUNTING_INTEGER),), actor=ACTOR)
    with pytest.raises(ValueError, match="integer capacity"):
        await store.apply_batch((usage_event("overflow"),), actor=ACTOR)
    assert await store.total(SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == MAX_ACCOUNTING_INTEGER


@pytest.mark.parametrize("amount", [0, -1, MAX_ACCOUNTING_INTEGER + 1])
def test_invalid_consumption_is_explicit(amount: int) -> None:
    """Invalid deltas never become resets or unlimited accounting."""
    with pytest.raises(ValueError, match="positive signed"):
        UsageCharge(MEDIA_BUCKET, amount)


def test_event_dimensions_are_unambiguous_and_window_boundaries_exact() -> None:
    """Fixed UTC windows avoid wall-clock sleeps and duplicate dimension charges."""
    assert UsageWindow().start(119) == 60
    assert UsageWindow().start(120) == 120
    with pytest.raises(ValueError, match="technical event ID"):
        usage_event("https://private.invalid/message")
    with pytest.raises(ValueError, match="at most once"):
        replace(
            usage_event("duplicate-dimension"), charges=(UsageCharge(MEDIA_BUCKET, 1), UsageCharge(MEDIA_BUCKET, 2))
        )
    with pytest.raises(ValueError, match="explicit stable"):
        AccountingSubject(TargetKind.GROUP, USER)


async def test_concurrent_replay_and_distinct_events_serialize_without_double_charge(test_engine: AsyncEngine) -> None:
    """Use independent real PostgreSQL connections for concurrent replay and aggregate updates."""
    engine = test_engine
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(UserDB.__table__.insert().values(telegram_id=USER))
        store = SQLAlchemyUsageLedgerStore(sessions, SQLAlchemyAuditedTransaction(sessions), FEATURE_REGISTRY)
        results = await asyncio.gather(
            *[store.apply_batch((usage_event("concurrent-replay"),), actor=ACTOR) for _index in range(4)]
        )
        assert sum(result.events_applied for result in results) == 1
        await asyncio.gather(
            *[store.apply_batch((usage_event(f"distinct-{index}"),), actor=ACTOR) for index in range(4)]
        )
        assert await store.total(SUBJECT, MEDIA_BUCKET, UsageUnit.REQUESTS, UsageWindow(), 90) == 5
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(UsageAccountingEventDB)) == 5
            assert await session.scalar(select(func.count()).select_from(AuditEventDB)) == 5
    finally:
        await engine.dispose()


async def test_resource_consumption_is_one_checkpoint_and_batch_audit(test_container: AsyncContainer) -> None:
    """Bulk resource units and multiple windows do not create one receipt or audit per token."""
    await usage_subjects(test_container)
    registry = FeatureRegistry(
        (
            FeatureDefinition(
                key="synthetic-resource",
                display_name="Synthetic resource accounting",
                usage_policy="synthetic-resource",
                units=frozenset({UsageUnit.TOKENS}),
            ),
        )
    )
    session_maker = await test_container.get(async_sessionmaker)
    store = SQLAlchemyUsageLedgerStore(session_maker, await test_container.get(AuditedUnitOfWork), registry)
    event = UsageEvent(
        "resource:checkpoint-1",
        SUBJECT,
        90,
        (
            UsageCharge("feature:synthetic-resource", 100_000, UsageUnit.TOKENS, UsageWindow(3600)),
            UsageCharge("family:synthetic-resource", 100_000, UsageUnit.TOKENS, UsageWindow(3600)),
        ),
    )
    assert await store.apply_batch((event,), actor=ACTOR) == AppliedUsage(1, 2, 200_000)
    assert await store.total(SUBJECT, "family:synthetic-resource", UsageUnit.TOKENS, UsageWindow(3600), 3599) == 100_000
    assert await store.total(SUBJECT, "family:synthetic-resource", UsageUnit.TOKENS, UsageWindow(3600), 3600) == 0
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(UsageAccountingEventDB)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEventDB).where(AuditEventDB.target_type == "usage_batch")
            )
            == 1
        )
